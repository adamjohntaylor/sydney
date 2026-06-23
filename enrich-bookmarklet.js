(function() {
  // Detect which site we're on
  const isDomain = location.hostname.includes('domain.com.au');
  const isREA = location.hostname.includes('realestate.com.au');

  if (!isDomain && !isREA) {
    alert('This bookmarklet only works on Domain or REA listing pages.');
    return;
  }

  const data = { url: location.href };

  if (isDomain) {
    // Extract from Domain listing page

    // Try to get structured data first
    const ldJson = document.querySelector('script[type="application/ld+json"]');
    let structured = null;
    if (ldJson) {
      try {
        structured = JSON.parse(ldJson.textContent);
        if (Array.isArray(structured)) structured = structured[0];
      } catch (e) {}
    }

    // Cover image - find the actual listing photo, not generic Domain images
    // Try gallery images first (most reliable)
    const gallerySelectors = [
      '[data-testid="gallery"] img',
      '[data-testid="listing-details__gallery"] img',
      '.listing-details__gallery img',
      '[class*="gallery"] img',
      '[class*="carousel"] img',
      '[class*="hero"] img',
      'picture source[type="image/webp"]',
      'picture img'
    ];

    for (const sel of gallerySelectors) {
      const el = document.querySelector(sel);
      if (el) {
        const src = el.srcset ? el.srcset.split(',').pop().trim().split(' ')[0] : (el.src || el.getAttribute('srcset'));
        // Only use if it's a Domain static image (rimh2.domainstatic.com.au)
        if (src && src.includes('domainstatic.com.au')) {
          data.cover_image = src;
          break;
        }
      }
    }

    // Fallback to og:image only if it's a property image
    if (!data.cover_image) {
      const ogImage = document.querySelector('meta[property="og:image"]');
      if (ogImage && ogImage.content.includes('domainstatic.com.au')) {
        data.cover_image = ogImage.content;
      }
    }

    // Address / suburb / postcode from the Domain URL slug. Domain listing URLs
    // end "/<address>-<suburb>-<state>-<postcode>-<id>" (e.g.
    // "/40-high-street-balmain-nsw-2041-2019123456"), which is the single most
    // reliable identity source on the page - the visible heading is sometimes
    // truncated or absent. We use it as a FALLBACK (page title parsed first,
    // below) and always to back-fill the postcode, which geocoding relies on.
    // This matters most for the auto-add path: a new entry with no address can be
    // neither geocoded nor de-duplicated.
    const slugParse = (function () {
      const STATES = ['nsw', 'vic', 'qld', 'act', 'sa', 'wa', 'tas', 'nt'];
      const STREET_TYPES = ['street', 'st', 'road', 'rd', 'avenue', 'ave', 'lane',
        'ln', 'drive', 'dr', 'place', 'pl', 'crescent', 'cres', 'cr', 'parade',
        'pde', 'way', 'close', 'cl', 'court', 'ct', 'circuit', 'cct', 'boulevard',
        'blvd', 'terrace', 'tce', 'grove', 'gr', 'esplanade', 'esp', 'highway',
        'hwy', 'square', 'sq', 'row', 'walk', 'rise', 'glade', 'mews', 'quay',
        'crest', 'circle', 'cove', 'grange', 'gardens', 'gdns'];
      const m = location.pathname.match(/\/([a-z0-9\-]+?)-(\d{7,12})\/?$/i);
      if (!m) return null;
      const parts = m[1].toLowerCase().split('-').filter(Boolean);
      const stateIdx = parts.findIndex(p => STATES.includes(p));
      if (stateIdx < 1) return null;
      const postcode = /^\d{4}$/.test(parts[stateIdx + 1] || '') ? parts[stateIdx + 1] : null;
      // Last street-type token before the state marks the address/suburb boundary.
      let stIdx = -1;
      for (let i = stateIdx - 1; i >= 0; i--) {
        if (STREET_TYPES.includes(parts[i])) { stIdx = i; break; }
      }
      const titleCase = a => a.map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
      let address = null, suburb = null;
      if (stIdx >= 0 && stIdx < stateIdx - 1) {
        let addrTokens = parts.slice(0, stIdx + 1);
        // Unit form "5-40-high-street" -> "5/40 High Street".
        if (addrTokens.length >= 2 && /^\d+[a-z]?$/.test(addrTokens[0]) && /^\d+[a-z]?$/.test(addrTokens[1])) {
          addrTokens = [addrTokens[0] + '/' + addrTokens[1]].concat(addrTokens.slice(2));
        }
        address = titleCase(addrTokens);
        suburb = titleCase(parts.slice(stIdx + 1, stateIdx));
      } else {
        // No recognised street-type: treat everything before the state as suburb.
        suburb = titleCase(parts.slice(0, stateIdx));
      }
      return { address, suburb, postcode };
    })();

    // Address from page title or heading
    const title = document.querySelector('h1[data-testid="listing-details__summary-title"], h1.listing-details__summary-title, h1');
    if (title) {
      const titleText = title.textContent.trim();
      // Parse "40 High Street, Balmain" or similar
      const addrMatch = titleText.match(/^(.+?),\s*([A-Za-z\s]+?)(?:\s+NSW|\s+\d{4}|$)/);
      if (addrMatch) {
        data.address = addrMatch[1].trim();
        data.suburb = addrMatch[2].trim();
      }
    }

    // Back-fill from the URL slug where the heading parse left a gap. Postcode is
    // only available from the slug, so always take it from there when present.
    if (slugParse) {
      if (!data.address && slugParse.address) data.address = slugParse.address;
      if (!data.suburb && slugParse.suburb) data.suburb = slugParse.suburb;
      if (slugParse.postcode) data.postcode = slugParse.postcode;
    }

    // Beds, baths, parking - try multiple methods

    // Method 1: Look for feature elements
    const features = document.querySelectorAll('[data-testid="property-features__feature"], .property-features__feature, [class*="property-feature"], [class*="Feature"]');
    features.forEach(f => {
      const text = f.textContent.toLowerCase();
      const num = parseInt(f.textContent);
      if (!isNaN(num)) {
        if (text.includes('bed')) data.beds = num;
        else if (text.includes('bath')) data.baths = num;
        else if (text.includes('parking') || text.includes('car') || text.includes('garage')) data.parking = num;
      }
    });

    // Method 2: Search page text for patterns like "3 Beds" or "3 bed"
    if (!data.beds || !data.baths || !data.parking) {
      const pageText = document.body.innerText;
      if (!data.beds) {
        const bedMatch = pageText.match(/(\d+)\s*[Bb]ed/);
        if (bedMatch) data.beds = parseInt(bedMatch[1]);
      }
      if (!data.baths) {
        const bathMatch = pageText.match(/(\d+)\s*[Bb]ath/);
        if (bathMatch) data.baths = parseInt(bathMatch[1]);
      }
      if (!data.parking) {
        const parkMatch = pageText.match(/(\d+)\s*(?:[Pp]arking|[Cc]ar|[Gg]arage)/);
        if (parkMatch) data.parking = parseInt(parkMatch[1]);
      }
    }

    // Method 3: Try structured data
    if (structured) {
      if (!data.beds && structured.numberOfBedrooms) data.beds = structured.numberOfBedrooms;
      if (!data.baths && structured.numberOfBathroomsTotal) data.baths = structured.numberOfBathroomsTotal;
    }

    // Property type
    const propType = document.querySelector('[data-testid="listing-summary-property-type"]');
    if (propType) {
      data.property_type = propType.textContent.toLowerCase().trim();
    }

    // Price - first try to find a HIDDEN price in the page source. Auction /
    // "Contact Agent" listings routinely omit the public guide but still embed the
    // agent's guide in the page JSON. We mine that and flag it "(hidden guide)" so
    // it shows as a guide needing re-verification, never a confirmed figure.
    let foundPrice = false;
    const pageSource = document.documentElement.innerHTML;
    const inGuard = (n) => Number.isFinite(n) && n >= 100000 && n <= 50000000;

    // 1) Schema.org offers.price from any JSON-LD block (most reliable, structured).
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
      if (foundPrice) return;
      try {
        let j = JSON.parse(s.textContent);
        (Array.isArray(j) ? j : [j]).forEach(o => {
          if (foundPrice || !o) return;
          const offers = Array.isArray(o.offers) ? o.offers[0] : o.offers;
          const raw = offers && (offers.price != null ? offers.price : offers.lowPrice);
          const num = parseInt(String(raw).replace(/[^\d]/g, ''), 10);
          if (inGuard(num)) {
            data.price_guide_text = `$${num.toLocaleString()} (hidden guide)`;
            console.log('Found hidden price (JSON-LD offers):', data.price_guide_text);
            foundPrice = true;
          }
        });
      } catch (e) {}
    });

    // 2) Hidden price fields in Domain's embedded JSON (__NEXT_DATA__/dataLayer).
    //    Kept guard-railed so a stray strata/land/sold number can't slip through.
    const hiddenPricePatterns = [
      /"exactPriceV2"\s*:\s*"?(\d{6,8})/i,
      /"exactPrice"\s*:\s*"?(\d{6,8})/i,
      /"exact"\s*:\s*"?(\d{6,8})/i,
      /"priceInt"\s*:\s*"?(\d{6,8})/i,
      /"priceFrom"\s*:\s*"?(\d{6,8})/i,
      /"priceTo"\s*:\s*"?(\d{6,8})/i,
      /"displayPriceFrom"\s*:\s*"?(\d{6,8})/i,
      /"searchPrice"\s*:\s*"?(\d{6,8})/i,
      /"price"\s*:\s*\{\s*"from"\s*:\s*"?(\d{6,8})/i
    ];

    if (!foundPrice) {
      for (const pattern of hiddenPricePatterns) {
        const match = pageSource.match(pattern);
        if (match) {
          const num = parseInt(match[1]);
          if (inGuard(num)) {
            data.price_guide_text = `$${num.toLocaleString()} (hidden guide)`;
            console.log('Found hidden price:', pattern, data.price_guide_text);
            foundPrice = true;
            break;
          }
        }
      }
    }

    // Fall back to visible text on page
    if (!foundPrice) {
      const topText = document.body.innerText.substring(0, 2500);
      const cleanText = topText.replace(/(?:last\s+)?sold\s+(?:in|for|on)[^]*/i, '');

      const pricePatterns = [
        /(?:Price Guide|Guide)[:\s]*\$([\d,]+)/i,
        /(?:Offers? (?:Over|Above|From))[:\s]*\$([\d,]+)/i,
        /\$([\d,]+)\s*(?:to|-)\s*\$([\d,]+)/i
      ];

      for (const pattern of pricePatterns) {
        const match = cleanText.match(pattern);
        if (match && match[1]) {
          const num = parseInt(match[1].replace(/,/g, ''));
          if (num >= 100000 && num <= 50000000) {
            data.price_guide_text = match[0].trim();
            console.log('Found visible price:', data.price_guide_text);
            foundPrice = true;
            break;
          }
        }
      }

      // If no price found, check if it's an Auction or Contact Agent
      if (!foundPrice) {
        if (/\bAuction\b/i.test(cleanText)) {
          data.price_guide_text = 'Auction - No price guide offered';
          console.log('Detected Auction with no price guide');
        } else if (/\bContact\s*Agent\b/i.test(cleanText)) {
          data.price_guide_text = 'Contact Agent';
          console.log('Detected Contact Agent');
        }
      }
    }

    // Description
    const desc = document.querySelector('[data-testid="listing-details__description"], .listing-details__description');
    if (desc) {
      data.description = desc.textContent.trim().substring(0, 2000);
    }

  } else if (isREA) {
    // Extract from REA listing page

    // Cover image - find actual listing photo
    const reaSelectors = [
      '[class*="gallery"] img',
      '[class*="carousel"] img',
      '[class*="hero"] img',
      '[class*="media"] img',
      'picture img'
    ];

    for (const sel of reaSelectors) {
      const el = document.querySelector(sel);
      if (el) {
        const src = el.src || el.getAttribute('srcset')?.split(',').pop().trim().split(' ')[0];
        // REA images come from their CDN
        if (src && (src.includes('reastatic.net') || src.includes('realestate.com.au'))) {
          data.cover_image = src;
          break;
        }
      }
    }

    // Fallback to og:image
    if (!data.cover_image) {
      const ogImage = document.querySelector('meta[property="og:image"]');
      if (ogImage && !ogImage.content.includes('logo')) {
        data.cover_image = ogImage.content;
      }
    }

    // Address from breadcrumb or title
    const addrEl = document.querySelector('[class*="property-info"] h1, [class*="address"]');
    if (addrEl) {
      const text = addrEl.textContent.trim();
      const addrMatch = text.match(/^(.+?),\s*([A-Za-z\s]+?)(?:\s+NSW|\s+\d{4}|,|$)/);
      if (addrMatch) {
        data.address = addrMatch[1].trim();
        data.suburb = addrMatch[2].trim();
      }
    }

    // Features
    const featureEls = document.querySelectorAll('[class*="feature"], [class*="general-features"] span');
    featureEls.forEach(f => {
      const text = f.textContent.toLowerCase();
      const numMatch = text.match(/(\d+)/);
      if (numMatch) {
        const num = parseInt(numMatch[1]);
        if (text.includes('bed')) data.beds = num;
        else if (text.includes('bath')) data.baths = num;
        else if (text.includes('car') || text.includes('parking') || text.includes('garage')) data.parking = num;
      }
    });

    // Property type
    const typeEl = document.querySelector('[class*="property-type"]');
    if (typeEl) {
      data.property_type = typeEl.textContent.toLowerCase().trim();
    }

    // Price - first try hidden price in REA source (uses "marketing_price" field)
    let reaFoundPrice = false;
    const reaPageSource = document.documentElement.innerHTML;
    const reaInGuard = (n) => Number.isFinite(n) && n >= 100000 && n <= 50000000;

    // Schema.org offers.price from JSON-LD (reliable, structured) - mined even for
    // auction/contact-agent listings and flagged "(hidden guide)".
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
      if (reaFoundPrice) return;
      try {
        let j = JSON.parse(s.textContent);
        (Array.isArray(j) ? j : [j]).forEach(o => {
          if (reaFoundPrice || !o) return;
          const offers = Array.isArray(o.offers) ? o.offers[0] : o.offers;
          const raw = offers && (offers.price != null ? offers.price : offers.lowPrice);
          const num = parseInt(String(raw).replace(/[^\d]/g, ''), 10);
          if (reaInGuard(num)) {
            data.price_guide_text = `$${num.toLocaleString()} (hidden guide)`;
            console.log('Found hidden price (REA JSON-LD offers):', data.price_guide_text);
            reaFoundPrice = true;
          }
        });
      } catch (e) {}
    });

    // Look for hidden "marketing_price" in REA's source
    const marketingMatch = reaFoundPrice ? null : reaPageSource.match(/"marketing_price"\s*:\s*"?\$?([\d,]+)/i);
    if (marketingMatch) {
      const num = parseInt(marketingMatch[1].replace(/,/g, ''));
      if (num >= 100000 && num <= 50000000) {
        data.price_guide_text = `$${num.toLocaleString()} (hidden guide)`;
        console.log('Found hidden marketing_price:', data.price_guide_text);
        reaFoundPrice = true;
      }
    }

    // Also try "price" fields in JSON
    if (!reaFoundPrice) {
      const reaPriceMatch = reaPageSource.match(/"(?:price|priceText|displayPrice)"\s*:\s*"?\$?([\d,]+)/i);
      if (reaPriceMatch) {
        const num = parseInt(reaPriceMatch[1].replace(/,/g, ''));
        if (num >= 100000 && num <= 50000000) {
          data.price_guide_text = `$${num.toLocaleString()} (hidden guide)`;
          console.log('Found hidden REA price:', data.price_guide_text);
          reaFoundPrice = true;
        }
      }
    }

    // Fall back to visible price on page
    if (!reaFoundPrice) {
      const reaPriceSelectors = [
        '[class*="property-price"]',
        '[class*="Price__price"]',
        '[data-testid="price"]'
      ];
      for (const sel of reaPriceSelectors) {
        const priceEl = document.querySelector(sel);
        if (priceEl) {
          const priceText = priceEl.textContent.trim();
          if (/\$[\d,]+/.test(priceText)) {
            const numMatch = priceText.match(/\$([\d,]+)/);
            if (numMatch) {
              const num = parseInt(numMatch[1].replace(/,/g, ''));
              if (num >= 100000 && num <= 50000000) {
                data.price_guide_text = priceText;
                reaFoundPrice = true;
                break;
              }
            }
          } else if (/^\s*(contact|auction|offers|expressions)/i.test(priceText)) {
            data.price_guide_text = priceText;
            reaFoundPrice = true;
            break;
          }
        }
      }
    }

    // Description
    const descEl = document.querySelector('[class*="description"]');
    if (descEl) {
      data.description = descEl.textContent.trim().substring(0, 2000);
    }
  }

  // Property features list (structured chips) + JSON-LD amenities. These are far
  // more reliable than parsing prose for accessibility signals like "Lift".
  const features = new Set();
  const featSelectors = [
    '[data-testid="listing-details__additional-features"] li',
    '[class*="additional-features"] li',
    '[data-testid="property-features"] li',
    '[class*="property-features"] li',
    '[class*="featureList"] li',
    '[class*="feature-list"] li',
    'ul[class*="feature"] li'
  ];
  featSelectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(li => {
      const t = (li.textContent || '').trim();
      if (t && t.length <= 40) features.add(t);
    });
  });
  // JSON-LD amenityFeature (any ld+json block on the page; also grabs numberOfRooms etc.)
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
    try {
      let j = JSON.parse(s.textContent);
      (Array.isArray(j) ? j : [j]).forEach(o => {
        const af = o && o.amenityFeature;
        if (Array.isArray(af)) af.forEach(a => { if (a && a.name) features.add(String(a.name).trim()); });
        if (o && o.floorLevel) data.floor = String(o.floorLevel).trim();
      });
    } catch (e) {}
  });
  // Accessibility safety net: scan the FULL description text (not the truncated
  // copy) for a building lift / step-free phrasing and record it as a feature, so
  // the signal survives even when "lift access" sits deep in a long bullet list.
  const descFull = document.querySelector(
    '[data-testid="listing-details__description"], .listing-details__description, [class*="description"]');
  const fullText = (descFull ? descFull.textContent : document.body.innerText) || '';
  if (/\b(?:lift\s+(?:access|lobby|to\s+(?:all|every|each|the|both|ground))|elevator|(?:secure|internal|passenger|residents'?|building'?s?|common)\s+lift|with\s+(?:a\s+)?lift)\b/i.test(fullText)
      && !/\b(?:no\s+lift|without\s+(?:a\s+)?lift|walk[\s-]?up)\b/i.test(fullText)) {
    features.add('Lift (listed)');
  }
  if (/\b(?:step[\s-]?free|level access|wheelchair access|disabled access|ramp access)\b/i.test(fullText)) {
    features.add('Step-free access (listed)');
  }
  if (features.size) data.features = Array.from(features).slice(0, 40);

  // Show what we found
  console.log('Extracted data:', data);

  // Encode data and open localhost page (avoids HTTPS->HTTP fetch blocking)
  const encoded = encodeURIComponent(JSON.stringify(data));
  window.open('http://localhost:8777/enrich-submit.html?data=' + encoded, '_blank', 'width=500,height=760');
})();
