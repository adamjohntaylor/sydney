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

    // Address from URL or title
    const urlMatch = location.pathname.match(/\/([^\/]+)-(\d{7,12})$/);
    if (urlMatch) {
      const addrSlug = urlMatch[1];
      // Parse address from slug: "40-high-street-balmain-nsw-2041"
      const parts = addrSlug.split('-');
      const postcodeIdx = parts.findIndex(p => /^\d{4}$/.test(p));
      if (postcodeIdx > 0) {
        const stateIdx = postcodeIdx - 1;
        const suburbStart = parts.slice(0, stateIdx).findIndex(p => /^[a-z]+$/.test(p) && !['street','st','road','rd','avenue','ave','lane','drive','place','crescent','parade','way','close','court','circuit','boulevard','terrace'].includes(p));
        // This is tricky - let's use the page title instead
      }
    }

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

    // Price - first try to find hidden price in page source (Domain uses "exact" field)
    let foundPrice = false;
    const pageSource = document.documentElement.innerHTML;

    // Look for hidden price fields in Domain's source
    const hiddenPricePatterns = [
      /"exactPriceV2"\s*:\s*(\d+)/i,
      /"exactPrice"\s*:\s*(\d+)/i,
      /"exact"\s*:\s*(\d+)/i,
      /"priceInt"\s*:\s*(\d+)/i,
      /"priceFrom"\s*:\s*(\d+)/i
    ];

    for (const pattern of hiddenPricePatterns) {
      const match = pageSource.match(pattern);
      if (match) {
        const num = parseInt(match[1]);
        if (num >= 100000 && num <= 50000000) {
          data.price_guide_text = `$${num.toLocaleString()} (hidden guide)`;
          console.log('Found hidden price:', pattern, data.price_guide_text);
          foundPrice = true;
          break;
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
      data.description = desc.textContent.trim().substring(0, 500);
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

    // Look for hidden "marketing_price" in REA's source
    const marketingMatch = reaPageSource.match(/"marketing_price"\s*:\s*"?\$?([\d,]+)/i);
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
      data.description = descEl.textContent.trim().substring(0, 500);
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
  if (features.size) data.features = Array.from(features).slice(0, 40);

  // Show what we found
  console.log('Extracted data:', data);

  // Encode data and open localhost page (avoids HTTPS->HTTP fetch blocking)
  const encoded = encodeURIComponent(JSON.stringify(data));
  window.open('http://localhost:8777/enrich-submit.html?data=' + encoded, '_blank', 'width=500,height=760');
})();
