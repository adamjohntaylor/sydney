(function() {
  // Detect which site we're on
  const isDomain = location.hostname.includes('domain.com.au');
  const isREA = location.hostname.includes('realestate.com.au');

  if (!isDomain && !isREA) {
    alert('This bookmarklet only works on Domain or REA listing pages.');
    return;
  }

  const data = { url: location.href };

  // Everything that reads the page is wrapped so a single failed selector on an
  // unfamiliar layout (REA changes its markup often) can never abort the whole
  // run silently - we still open the submit popup below with whatever we have.
  try {

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
      const topText = document.body.innerText.substring(0, 3000);
      const cleanText = topText.replace(/(?:last\s+)?sold\s+(?:in|for|on)[^]*/i, '');

      // A dollar amount written any common way: $1,800,000 / $1.8m / $1.8 million / $950k.
      // The old `\$([\d,]+)` capture stopped at the decimal, so "Buyer's Guide $1.8m"
      // was read as "$1" and rejected by the guard. AMT captures decimals + suffix.
      const AMT = "\\$\\s*[\\d,]+(?:\\.\\d+)?\\s*(?:million|mil|m|k)?";
      const toNum = (s) => {
        if (!s) return null;
        s = String(s).toLowerCase().replace(/[\$,\s]/g, '');
        let mult = 1;
        if (/(?:million|mil|m)$/.test(s)) { mult = 1e6; s = s.replace(/(?:million|mil|m)$/, ''); }
        else if (/k$/.test(s)) { mult = 1e3; s = s.replace(/k$/, ''); }
        const v = parseFloat(s);
        return isNaN(v) ? null : Math.round(v * mult);
      };

      // (1) A labelled guide - now incl. "Buyer's Guide" and abbreviated/range amounts.
      const labelRe = new RegExp(
        "(?:buyer'?s?\\s+guide|price\\s+guide|guide|offers?\\s+(?:over|above|from)|asking|eoi|expressions?\\s+of\\s+interest)" +
        "[^$\\d\\n]{0,15}(" + AMT + "(?:\\s*(?:to|-|\\u2013|\\u2014)\\s*" + AMT + ")?)", "i");
      const lm = cleanText.match(labelRe);
      if (lm && lm[1]) {
        const firstAmt = (lm[1].match(new RegExp(AMT, "i")) || [])[0];
        const n = toNum(firstAmt);
        if (n && n >= 100000 && n <= 50000000) {
          data.price_guide_text = lm[0].replace(/\s+/g, ' ').trim();
          console.log('Found visible price (label):', data.price_guide_text);
          foundPrice = true;
        }
      }

      // (2) A bare $amount range with no label (e.g. "$1,800,000 - $1,900,000").
      if (!foundPrice) {
        const rangeRe = new RegExp("(" + AMT + ")\\s*(?:to|-|\\u2013|\\u2014)\\s*(" + AMT + ")", "i");
        const rm = cleanText.match(rangeRe);
        if (rm) {
          const n = toNum((rm[0].match(new RegExp(AMT, "i")) || [])[0]);
          if (n && n >= 100000 && n <= 50000000) {
            data.price_guide_text = rm[0].replace(/\s+/g, ' ').trim();
            console.log('Found visible price (range):', data.price_guide_text);
            foundPrice = true;
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

    // Beds / baths / parking / size. REA renders these as SVG-icon chips whose
    // CSS classes change frequently, so we read REA's OWN embedded data layer and
    // element aria-labels (its accessibility labels are stable) rather than
    // fragile class selectors. Tried in order of reliability.
    const reaSrc = document.documentElement.innerHTML;
    const grabNum = (patterns, lo, hi) => {
      for (const re of patterns) {
        const m = reaSrc.match(re);
        if (m) { const n = parseInt(m[1], 10); if (n >= lo && n <= hi) return n; }
      }
      return null;
    };
    // 1) Embedded data layer. Handles both the nested
    //    "generalFeatures":{"bedrooms":{"value":2},...} shape and a flat
    //    "bedrooms":2 / "parkingSpaces":1 / "carspaces":1 shape.
    if (data.beds == null) data.beds = grabNum([
      /"bedrooms"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i,
      /"bedrooms"\s*:\s*"?(\d+)"?/i,
      /"beds"\s*:\s*"?(\d+)"?/i
    ], 0, 20);
    if (data.baths == null) data.baths = grabNum([
      /"bathrooms"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i,
      /"bathrooms"\s*:\s*"?(\d+)"?/i,
      /"baths"\s*:\s*"?(\d+)"?/i
    ], 0, 20);
    if (data.parking == null) data.parking = grabNum([
      /"parkingSpaces"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i,
      /"parkingSpaces"\s*:\s*"?(\d+)"?/i,
      /"carspaces"\s*:\s*"?(\d+)"?/i,
      /"carSpaces"\s*:\s*"?(\d+)"?/i
    ], 0, 20);
    if (!data.internal_m2) {
      const m2 = grabNum([
        /"building"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?(\d+)/i,
        /"buildingSize"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i,
        /"propertySizes"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?(\d+)/i
      ], 10, 100000);
      if (m2) data.internal_m2 = m2;
    }
    // 2) aria-label / title fallback - REA's accessible feature chips, e.g.
    //    "2 bedrooms", "2 Bathrooms", "1 car space".
    if (data.beds == null || data.baths == null || data.parking == null) {
      document.querySelectorAll('[aria-label],[title]').forEach(el => {
        const l = (el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase();
        if (l.length > 40) return;
        const m = l.match(/(\d+)/); if (!m) return;
        const n = parseInt(m[1], 10); if (n < 0 || n > 20) return;
        if (data.beds == null && /\bbed(?:room)?s?\b/.test(l)) data.beds = n;
        else if (data.baths == null && /\bbath(?:room)?s?\b/.test(l)) data.baths = n;
        else if (data.parking == null && /\b(?:car|parking|garage)\b/.test(l)) data.parking = n;
      });
    }
    // 3) Last resort: the old class-based chip scan (kept as a final fallback).
    if (data.beds == null || data.baths == null || data.parking == null) {
      document.querySelectorAll('[class*="feature"], [class*="general-features"] span').forEach(f => {
        const text = (f.textContent || '').toLowerCase();
        const numMatch = text.match(/(\d+)/);
        if (!numMatch) return;
        const num = parseInt(numMatch[1], 10);
        if (data.beds == null && text.includes('bed')) data.beds = num;
        else if (data.baths == null && text.includes('bath')) data.baths = num;
        else if (data.parking == null && (text.includes('car') || text.includes('parking') || text.includes('garage'))) data.parking = num;
      });
    }

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

    // REA structures price differently from Domain. Domain leaks a flat numeric
    // "exactPrice"; REA NESTS it in a price object ("price":{"value":N,"display":"…"})
    // and/or exposes a display string ("displayPrice"/"priceText"). The old
    // `"price":<number>` regex never matched REA's `"price":{…}`, so price came back
    // empty. Mine several REA shapes, preferring a real number (flagged "(hidden
    // guide)") and otherwise taking the agent's display text verbatim - the server's
    // parse_price turns "$1.85m"/ranges into numeric bounds and keeps "Contact
    // Agent"/"Auction" as honest no-number placeholders.
    if (!reaFoundPrice) {
      const src = reaPageSource;
      // (a) numeric value: nested price object, priceDetails, or a flat numeric field.
      const numPats = [
        /"price"\s*:\s*\{[^{}]{0,160}?"value"\s*:\s*"?(\d{5,8})/i,
        /"priceDetails"\s*:\s*\{[^{}]{0,200}?"price"\s*:\s*"?(\d{5,8})/i,
        /"(?:displayPrice|exactPrice|searchPrice|priceFrom|priceValue|filterablePrice)"\s*:\s*"?(\d{5,8})\b/i,
        /"price"\s*:\s*"?(\d{5,8})\b/i
      ];
      for (const re of numPats) {
        const m = src.match(re);
        if (m) {
          const n = parseInt(m[1], 10);
          if (reaInGuard(n)) { data.price_guide_text = `$${n.toLocaleString()} (hidden guide)`; reaFoundPrice = true; break; }
        }
      }
      // (a2) display string INSIDE a nested price object (e.g. "$1.85m", "Contact Agent").
      if (!reaFoundPrice) {
        const m = src.match(/"price"\s*:\s*\{[^{}]{0,200}?"(?:display|displayText|displayPrice|label|text|advertised)"\s*:\s*"([^"]{1,60})"/i);
        if (m && /(\$|\d|contact|auction|offer|expression|eoi|guide|price on|under offer)/i.test(m[1])) {
          data.price_guide_text = m[1].trim(); reaFoundPrice = true;
        }
      }
      // (b) a display string carrying a $ amount under any "...price..."-ish key.
      if (!reaFoundPrice) {
        const m = src.match(/"(?:[a-zA-Z]*[Pp]rice[a-zA-Z]*)"\s*:\s*"([^"]{0,60}\$[^"]{0,60})"/);
        if (m) { data.price_guide_text = m[1].trim(); reaFoundPrice = true; }
      }
      // (c) a no-number guide string (Contact Agent / Auction / Offers / EOI / Guide).
      if (!reaFoundPrice) {
        const m = src.match(/"(?:displayPrice|priceText|priceDisplay|price)"\s*:\s*"((?:contact|auction|offers|expressions|eoi|guide|price on|under offer)[^"]{0,40})"/i);
        if (m) { data.price_guide_text = m[1].trim(); reaFoundPrice = true; }
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

    // (d) Last resort: REA's meta / og description usually restates the guide.
    if (!reaFoundPrice) {
      const md = (document.querySelector('meta[name="description"], meta[property="og:description"]') || {}).content || '';
      const m = md.match(/\$\s*[\d,]+(?:\.\d+)?\s*[kKmM]?(?:\s*-\s*\$?\s*[\d,]+(?:\.\d+)?\s*[kKmM]?)?/);
      if (m) { data.price_guide_text = m[0].trim(); reaFoundPrice = true; }
    }

    // Description
    const descEl = document.querySelector('[class*="description"]');
    if (descEl) {
      data.description = descEl.textContent.trim().substring(0, 2000);
    }

    // REA URL fallback. realestate.com.au listing URLs are
    // "/property-<type>-<state>-<suburb>-<id>" (e.g.
    // "/property-apartment-nsw-glebe-150693264"). They carry no street address or
    // postcode, but the suburb and type are reliable - and REA renders
    // client-side and renames its CSS classes often, so the DOM selectors above
    // routinely miss. Use the URL so a REA listing always has at least a suburb +
    // type identity (the JSON-LD block below adds the street address when present).
    if (!data.suburb || !data.property_type) {
      const m = location.pathname.match(/\/property-([a-z]+)-[a-z]{2,3}-([a-z0-9-]+?)-(\d{6,12})\/?$/i);
      if (m) {
        const tc = s => s.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
        if (!data.property_type && m[1]) data.property_type = m[1].toLowerCase();
        if (!data.suburb && m[2]) data.suburb = tc(m[2]);
      }
    }
  }

  // Cross-site structured-data fallback (schema.org JSON-LD). REA in particular
  // renders client-side and changes its CSS class names, so the markup-based
  // selectors above miss; the embedded JSON-LD is the stable, standards-based
  // source. We back-fill address / suburb / postcode / beds / baths / geo ONLY
  // where the site-specific extraction left a gap, walking @graph and nested
  // arrays so it survives either site's envelope shape. geo coords, when present,
  // let a brand-new listing be Tier-1 scored without the server-side geocoder.
  (function () {
    const want = k => data[k] === undefined || data[k] === null || data[k] === '';
    const nodes = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
      try {
        const push = o => {
          if (!o || typeof o !== 'object') return;
          nodes.push(o);
          if (Array.isArray(o['@graph'])) o['@graph'].forEach(push);
        };
        const j = JSON.parse(s.textContent);
        (Array.isArray(j) ? j : [j]).forEach(push);
      } catch (e) {}
    });
    for (const o of nodes) {
      const a = o.address && typeof o.address === 'object'
        ? (Array.isArray(o.address) ? o.address[0] : o.address) : null;
      if (a) {
        if (want('address') && a.streetAddress) data.address = String(a.streetAddress).trim();
        if (want('suburb') && a.addressLocality) data.suburb = String(a.addressLocality).trim();
        if (want('postcode') && a.postalCode) data.postcode = String(a.postalCode).trim();
      }
      if (want('beds') && o.numberOfBedrooms != null) {
        const n = parseInt(o.numberOfBedrooms, 10); if (!isNaN(n)) data.beds = n;
      }
      if (want('baths')) {
        const rawB = o.numberOfBathroomsTotal != null ? o.numberOfBathroomsTotal : o.numberOfBathrooms;
        if (rawB != null) { const n = parseInt(rawB, 10); if (!isNaN(n)) data.baths = n; }
      }
      const g = o.geo && typeof o.geo === 'object' ? o.geo : null;
      if (g && want('lat') && g.latitude != null && g.longitude != null) {
        const la = parseFloat(g.latitude), lo = parseFloat(g.longitude);
        if (!isNaN(la) && !isNaN(lo)) { data.lat = la; data.lon = lo; }
      }
    }
  })();

  // ---- Floor-area + facts reader (identical copy in extension/content.js) ----
  // Reads the property's INTERNAL (floor/building) area and LAND area off the
  // listing page, keeping the two apart: Tier 1 needs internal_m2 >= 100 and a
  // land figure must never masquerade as it. Sources, most reliable first:
  //   1. the site's embedded data layer. Domain (verified live 4 Oct 2026 on
  //      1001/2 Cowper St Glebe): an analytics "property":{...} object near the
  //      top carries "buildingsize":149 and "internalArea":149, with the listing
  //      id a few hundred chars before it; the id-keyed listing block further down
  //      holds "landSize":N (0 = not stated) and no internal figure; the page also
  //      embeds OTHER listings' blocks ("similar properties"), so a match counts
  //      only when this listing's id sits within the preceding 2,000 chars. REA:
  //      "buildingSize"/"landSize" {value} objects, same proximity rule.
  //   2. JSON-LD floorSize / lotSize;
  //   3. visible text with an explicit label ("Internal 111m²", "450m² land") or
  //      Domain's FAQ line "The internal land size for <addr> is 149m²";
  //   4. an unlabelled "NNNm²" feature chip: an apartment has no land, so it is
  //      internal; for a house it is taken as land and never as internal.
  // `area_basis` records which source answered; `area_checked` says we looked.
  const readAreaFromPage = function (ptypeHint) {
    const out = { area_checked: true };
    const whole = document.documentElement.innerHTML;
    const lid = (location.href.match(/(?:-|\/)(\d{6,12})\/?(?:\?.*)?$/) || [])[1] || null;
    const num = (v) => { const n = parseInt(String(v).replace(/[^\d]/g, ''), 10); return Number.isFinite(n) ? n : null; };
    const okInt = (n) => n != null && n >= 20 && n <= 2000;
    const okLand = (n) => n != null && n >= 30 && n <= 200000;
    // First match that belongs to THIS listing: the id must appear in the 2,000
    // chars before it (no id known -> first match anywhere).
    const grabNear = (res, ok) => {
      for (const re of res) {
        const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
        let m;
        while ((m = g.exec(whole)) !== null) {
          const n = num(m[1]);
          if (!ok(n)) continue;
          if (!lid || whole.slice(Math.max(0, m.index - 2000), m.index).includes(lid)) return n;
        }
      }
      return null;
    };
    const INT_RES = [
      /"(?:buildingsize|buildingSize|buildingArea|floorArea|internalArea|internalSize|floorSize|livingArea)"\s*:\s*"?(\d{2,5})(?:\.\d+)?"?\s*[,}]/i,
      /"(?:buildingSize|buildingArea|floorArea|internalArea|floorSize)"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?(\d{2,5})/i,
      /"propertySizes"\s*:\s*\{\s*"building"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?(\d{2,5})/i
    ];
    const LAND_RES = [
      /"(?:landAreaSqm|landSize|landsize|landArea)"\s*:\s*"?([1-9]\d{1,5})(?:\.\d+)?"?\s*[,}]/i,
      /"(?:landSize|landArea|lotSize)"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?([1-9]\d{1,5})/i,
      /"propertySizes"\s*:\s*\{[^]*?"land"\s*:\s*\{[^{}]*?"(?:displayValue|value)"\s*:\s*"?([1-9]\d{1,5})/i
    ];
    const basis = [];
    let internal = grabNear(INT_RES, okInt);
    let land = grabNear(LAND_RES, okLand);
    if (internal) basis.push('page data: building/floor area');
    if (land) basis.push('page data: land area');
    // JSON-LD (unitCode MTK = square metres)
    if (!internal || !land) {
      const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent).join('\n');
      if (!internal) { const m = ld.match(/"floorSize"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d{2,5})/i); if (m && okInt(num(m[1]))) { internal = num(m[1]); basis.push('JSON-LD floorSize'); } }
      if (!land) { const m = ld.match(/"lotSize"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d{2,6})/i); if (m && okLand(num(m[1]))) { land = num(m[1]); basis.push('JSON-LD lotSize'); } }
    }
    // Visible text with an explicit label, either order ("Internal: 111m²", "111m² internal").
    const text = (document.body && (document.body.innerText || document.body.textContent)) || '';
    const M2 = '(?:m²|m2|sqm|sq\\.?\\s?m\\b|square\\s+met(?:re|er)s?)';
    const INT_WORDS = '(?:internal|interior|floor|living|building|total)(?:\\s+(?:floor\\s+)?(?:area|size|space))?';
    const LAND_WORDS = '(?:land|lot|block|site|allotment)(?:\\s+(?:area|size))?';
    const labelled = (words, ok) => {
      const a = text.match(new RegExp('\\b' + words + '\\s*[:\\-–]?\\s*(?:(?:of\\s+)?approx(?:imately|\\.)?\\s*)?(\\d{2,6})\\s*' + M2, 'i'));
      if (a && ok(num(a[1]))) return num(a[1]);
      const b = text.match(new RegExp('(\\d{2,6})\\s*' + M2 + '\\s*(?:\\(?approx\\.?\\)?\\s*)?(?:of\\s+)?' + words + '\\b', 'i'));
      if (b && ok(num(b[1]))) return num(b[1]);
      return null;
    };
    // Domain's generated FAQ: "The internal land size for <addr> is 149m²." (apartments)
    // / "The land size for <addr> is 450m²." (houses).
    if (!internal) { const m = text.match(new RegExp('internal(?:\\s+land)?\\s+size\\s+for\\s+[^\\n]{3,120}?\\s+is\\s+(\\d{2,5})\\s*' + M2, 'i')); if (m && okInt(num(m[1]))) { internal = num(m[1]); basis.push('page text: "internal size for ... is"'); } }
    if (!land) { const m = text.match(new RegExp('(?:^|[^l]\\s)land\\s+size\\s+for\\s+[^\\n]{3,120}?\\s+is\\s+(\\d{2,6})\\s*' + M2, 'i')); if (m && okLand(num(m[1])) && !/internal\s+land\s+size\s+for/i.test(text)) { land = num(m[1]); basis.push('page text: "land size for ... is"'); } }
    if (!internal) { const n = labelled(INT_WORDS, okInt); if (n) { internal = n; basis.push('page text: labelled internal/floor area'); } }
    if (!land) { const n = labelled(LAND_WORDS, okLand); if (n) { land = n; basis.push('page text: labelled land area'); } }
    // Unlabelled feature chip.
    if (!internal) {
      const pt = (ptypeHint || '').toLowerCase();
      const isApt = /apartment|unit|flat|studio|penthouse/.test(pt);
      const chip = text.match(new RegExp('(?:^|\\n|\\s)(\\d{2,4})\\s*' + M2 + '(?=\\s|$)', 'i'));
      const n = chip ? num(chip[1]) : null;
      if (n && okInt(n)) {
        if (isApt && n !== land) { internal = n; basis.push('page text: unlabelled m² figure (apartment ⇒ internal)'); }
        else if (!isApt && !land && okLand(n)) { land = n; basis.push('page text: unlabelled m² figure (house ⇒ land, not internal)'); }
      }
    }
    if (internal) out.internal_m2 = internal;
    if (land) out.land_m2 = land;
    out.area_basis = basis.length ? basis.join('; ') : 'no area figure found on the page';
    return out;
  };

  // Beds / baths / parking / property type for a listing page that the watchlist
  // only knows as an alert-derived search URL (108 of 123 active records on
  // 4 Oct 2026 had no beds or type). JSON-LD first, then the embedded data layer,
  // then the accessible feature chips.
  const readFactsFromPage = function () {
    const f = {};
    const src = document.documentElement.innerHTML;
    const lid = (location.href.match(/(?:-|\/)(\d{6,12})\/?(?:\?.*)?$/) || [])[1] || null;
    // REA's URL slug names the type precisely (property-apartment-nsw-..., property-
    // house-nsw-..., property-townhouse-...); its JSON-LD only says "Residence".
    { const m = location.pathname.match(/\/property-([a-z]+(?:-[a-z]+)?)-(?:nsw|vic|qld|sa|wa|tas|act|nt)-/i); if (m) f.property_type = m[1].toLowerCase().replace(/-/g, ' '); }
    // Same proximity rule as the area reader: the page embeds other listings'
    // data too, so a data-layer value counts only with this listing's id nearby.
    const grab = (res, lo, hi) => {
      for (const re of res) {
        const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
        let m;
        while ((m = g.exec(src)) !== null) {
          const n = parseInt(m[1], 10);
          if (!(n >= lo && n <= hi)) continue;
          if (!lid || src.slice(Math.max(0, m.index - 2000), m.index).includes(lid)) return n;
        }
      }
      return null;
    };
    const grabStr = (res) => {
      for (const re of res) {
        const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
        let m;
        while ((m = g.exec(src)) !== null) {
          if (!lid || src.slice(Math.max(0, m.index - 2000), m.index).includes(lid)) return m[1];
        }
      }
      return null;
    };
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
      try {
        const push = (o) => {
          if (!o || typeof o !== 'object') return;
          if (f.beds == null && o.numberOfBedrooms != null) { const n = parseInt(o.numberOfBedrooms, 10); if (!isNaN(n)) f.beds = n; }
          if (f.baths == null) { const r = o.numberOfBathroomsTotal != null ? o.numberOfBathroomsTotal : o.numberOfBathrooms; if (r != null) { const n = parseInt(r, 10); if (!isNaN(n)) f.baths = n; } }
          if (!f.property_type && typeof o['@type'] === 'string' && /^(Apartment|House|SingleFamilyResidence)$/i.test(o['@type'])) f.property_type = /house|residence/i.test(o['@type']) ? 'house' : 'apartment';
          if (Array.isArray(o['@graph'])) o['@graph'].forEach(push);
        };
        const j = JSON.parse(s.textContent); (Array.isArray(j) ? j : [j]).forEach(push);
      } catch (e) {}
    });
    if (f.beds == null) f.beds = grab([/"bedrooms"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i, /"bedrooms"\s*:\s*"?(\d+)"?/i, /"beds"\s*:\s*"?(\d+)"?/i], 0, 20);
    if (f.baths == null) f.baths = grab([/"bathrooms"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i, /"bathrooms"\s*:\s*"?(\d+)"?/i, /"baths"\s*:\s*"?(\d+)"?/i], 0, 20);
    if (f.parking == null) f.parking = grab([/"parkingSpaces"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+)/i, /"parkingSpaces"\s*:\s*"?(\d+)"?/i, /"carspaces"\s*:\s*"?(\d+)"?/i, /"carSpaces"\s*:\s*"?(\d+)"?/i], 0, 20);
    const typeEl = document.querySelector('[data-testid="listing-summary-property-type"], [class*="property-type"]');
    if (typeEl && typeEl.textContent.trim().length < 40) f.property_type = typeEl.textContent.trim().toLowerCase();
    if (!f.property_type) { const v = grabStr([/"propertyTypeFormatted"\s*:\s*"([^"]{2,40})"/, /"propertyType"\s*:\s*"([a-z ]{2,30})"/i]); if (v) f.property_type = v.toLowerCase(); }
    if (f.beds == null || f.baths == null || f.parking == null) {
      document.querySelectorAll('[aria-label],[title]').forEach(el => {
        const l = (el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase();
        if (l.length > 40) return;
        const m = l.match(/(\d+)/); if (!m) return;
        const n = parseInt(m[1], 10); if (n < 0 || n > 20) return;
        if (f.beds == null && /\bbed(?:room)?s?\b/.test(l)) f.beds = n;
        else if (f.baths == null && /\bbath(?:room)?s?\b/.test(l)) f.baths = n;
        else if (f.parking == null && /\b(?:car|parking|garage)\b/.test(l)) f.parking = n;
      });
    }
    Object.keys(f).forEach(k => { if (f[k] == null) delete f[k]; });
    return f;
  };

  // Floor area (both sites) - the same reader the Chrome extension's Verify run
  // uses, so bookmarklet and sweep agree. Keeps internal and land apart; the
  // REA-only buildingSize mining above still runs first where it applies.
  try {
    const facts = readFactsFromPage();
    for (const k of ['beds', 'baths', 'parking', 'property_type']) if (data[k] == null || data[k] === '') { if (facts[k] != null) data[k] = facts[k]; }
    const area = readAreaFromPage(data.property_type);
    if (area.internal_m2 && !data.internal_m2) data.internal_m2 = area.internal_m2;
    if (area.land_m2) data.land_m2 = area.land_m2;
    data.area_basis = (data.internal_m2 && !area.internal_m2) ? 'page data: building size' : area.area_basis;
    data.area_checked = true;
  } catch (e) { console.log('area reader failed:', e); }

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

  // ---- Market-status banner (Sold / Under offer / Withdrawn) --------------
  // Read the listing's OWN sale status so the dashboard can migrate it to the
  // Withdrawn/sold tab. Deliberately conservative: Domain/REA pages embed
  // "recently sold nearby" modules, so we key ONLY off listing-specific signals
  // (URL path, this listing's JSON-LD offer availability, short badge/tag
  // elements, the price display, and the very top of the page) - never a raw
  // page-source grep for the word "sold".
  (function () {
    let status = null, basis = null;

    // 1) URL path: REA serves sold listings under /sold/; Domain does not, but
    //    keep the check generic - it is the single strongest signal when present.
    if (/\/sold\//i.test(location.pathname)) {
      status = 'sold'; basis = 'URL path (/sold/)';
    }

    // 2) This listing's JSON-LD: schema.org offers.availability SoldOut.
    if (!status) {
      document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        if (status) return;
        try {
          const push = o => {
            if (!o || typeof o !== 'object' || status) return;
            const offers = Array.isArray(o.offers) ? o.offers[0] : o.offers;
            const avail = offers && String(offers.availability || '');
            if (/soldout/i.test(avail)) { status = 'sold'; basis = 'JSON-LD offers.availability'; }
            if (Array.isArray(o['@graph'])) o['@graph'].forEach(push);
          };
          const j = JSON.parse(s.textContent);
          (Array.isArray(j) ? j : [j]).forEach(push);
        } catch (e) {}
      });
    }

    // 3) Short badge/tag/banner elements ("SOLD", "Under offer", "Sold at
    //    auction 12 Jul"). Only elements with SHORT text qualify, so a
    //    description paragraph mentioning "sold" can never match.
    if (!status) {
      const badgeSel = '[data-testid*="tag"], [data-testid*="status"], [data-testid*="banner"], ' +
        '[class*="tag"], [class*="Tag"], [class*="badge"], [class*="Badge"], ' +
        '[class*="banner"], [class*="Banner"], [class*="status"], [class*="Status"]';
      const els = document.querySelectorAll(badgeSel);
      for (let i = 0; i < els.length && !status; i++) {
        const t = (els[i].textContent || '').trim();
        if (!t || t.length > 45) continue;
        if (/^sold\b/i.test(t) || /\bsold\s+(?:at\s+auction|prior\s+to\s+auction|on\s+\d)/i.test(t)) {
          status = 'sold'; basis = 'page badge: "' + t.slice(0, 40) + '"';
        } else if (/\bunder\s+(?:offer|contract)\b|\bsale\s+pending\b/i.test(t)) {
          status = 'under_offer'; basis = 'page badge: "' + t.slice(0, 40) + '"';
        }
      }
    }

    // 4) The price display itself often carries the state.
    if (!status && data.price_guide_text) {
      if (/^\s*sold\b/i.test(data.price_guide_text)) {
        status = 'sold'; basis = 'price display';
      } else if (/\bunder\s+(?:offer|contract)\b/i.test(data.price_guide_text)) {
        status = 'under_offer'; basis = 'price display';
      }
    }

    // 5) Top-of-page text: the hero banner region only (first 1,500 chars),
    //    with SOLD anchored to a sale phrase or the very start of the page.
    if (!status) {
      const top = (document.body.innerText || '').substring(0, 1500);
      if (/^\s*sold\b/i.test(top) || /\bsold\s+(?:at\s+auction|prior\s+to\s+auction|on\s+\d)/i.test(top)) {
        status = 'sold'; basis = 'page banner text';
      } else if (/\bunder\s+(?:offer|contract)\b|\bsale\s+pending\b/i.test(top)) {
        status = 'under_offer'; basis = 'page banner text';
      } else if (/no\s+longer\s+(?:available|advertised|on\s+the\s+market)|listing\s+has\s+been\s+removed|this\s+property\s+is\s+not\s+available/i.test(top)) {
        status = 'withdrawn'; basis = 'page banner text';
      }
    }

    data.listing_status = status || 'on_market';
    if (basis) data.listing_status_basis = basis;
    console.log('Market status:', data.listing_status, basis ? '(' + basis + ')' : '(no departure signal)');
  })();

  // Show what we found
  console.log('Extracted data:', data);

  } catch (e) {
    // Record the failure but DON'T abort - still hand off whatever we collected.
    data._error = String((e && e.message) || e);
    console.error('Enrich bookmarklet extraction error:', e);
  }

  // Encode data and open the localhost submit page. This ALWAYS runs (even if
  // extraction threw above), so a click is never a silent no-op. window.open of a
  // top-level localhost URL is not subject to mixed-content/CSP; if the popup is
  // nonetheless blocked, fall back to navigating this tab.
  try {
    const encoded = encodeURIComponent(JSON.stringify(data));
    const submitUrl = 'http://localhost:8777/enrich-submit.html?data=' + encoded;
    const w = window.open(submitUrl, '_blank', 'width=500,height=760');
    if (!w) {
      if (confirm('Could not open the dashboard popup (popup blocked?).\nClick OK to open it in this tab instead.')) {
        location.href = submitUrl;
      }
    }
  } catch (e2) {
    alert('Enrich bookmarklet could not reach the dashboard at http://localhost:8777 .\n'
        + 'Is the local server running (python scripts/serve.py)?\n\n' + ((e2 && e2.message) || e2));
  }
})();
