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

    // Beds, baths, parking from features
    const features = document.querySelectorAll('[data-testid="property-features__feature"], .property-features__feature, [class*="property-feature"]');
    features.forEach(f => {
      const text = f.textContent.toLowerCase();
      const num = parseInt(f.textContent);
      if (!isNaN(num)) {
        if (text.includes('bed')) data.beds = num;
        else if (text.includes('bath')) data.baths = num;
        else if (text.includes('parking') || text.includes('car') || text.includes('garage')) data.parking = num;
      }
    });

    // Also try structured data
    if (structured) {
      if (!data.beds && structured.numberOfBedrooms) data.beds = structured.numberOfBedrooms;
      if (!data.baths && structured.numberOfBathroomsTotal) data.baths = structured.numberOfBathroomsTotal;
    }

    // Property type
    const propType = document.querySelector('[data-testid="listing-summary-property-type"]');
    if (propType) {
      data.property_type = propType.textContent.toLowerCase().trim();
    }

    // Price - only capture if it looks like a real price
    const priceSelectors = [
      '[data-testid="listing-details__summary-title-price"]',
      '.listing-details__summary-title-price',
      '[data-testid="listing-details__summary-price"]'
    ];
    for (const sel of priceSelectors) {
      const priceEl = document.querySelector(sel);
      if (priceEl) {
        const priceText = priceEl.textContent.trim();
        // Only accept if it contains $ followed by digits, or specific keywords
        if (/\$[\d,]+/.test(priceText) || /^\s*(contact|auction|price guide|offers|expressions)/i.test(priceText)) {
          // Validate it's a reasonable property price ($100k - $50m)
          const numMatch = priceText.match(/\$([\d,]+)/);
          if (numMatch) {
            const num = parseInt(numMatch[1].replace(/,/g, ''));
            if (num >= 100000 && num <= 50000000) {
              data.price_guide_text = priceText;
              break;
            }
          } else if (/contact|auction/i.test(priceText)) {
            // Accept "Contact Agent" or "Auction" without a price
            data.price_guide_text = priceText;
            break;
          }
        }
        break;
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

    // Price - only capture valid property prices
    const reaPriceSelectors = [
      '[class*="property-price"]',
      '[class*="Price__price"]',
      '[data-testid="price"]'
    ];
    for (const sel of reaPriceSelectors) {
      const priceEl = document.querySelector(sel);
      if (priceEl) {
        const priceText = priceEl.textContent.trim();
        // Only accept if it contains $ followed by digits, or specific keywords
        if (/\$[\d,]+/.test(priceText)) {
          const numMatch = priceText.match(/\$([\d,]+)/);
          if (numMatch) {
            const num = parseInt(numMatch[1].replace(/,/g, ''));
            if (num >= 100000 && num <= 50000000) {
              data.price_guide_text = priceText;
              break;
            }
          }
        } else if (/^\s*(contact|auction|offers|expressions)/i.test(priceText)) {
          data.price_guide_text = priceText;
          break;
        }
      }
    }

    // Description
    const descEl = document.querySelector('[class*="description"]');
    if (descEl) {
      data.description = descEl.textContent.trim().substring(0, 500);
    }
  }

  // Show what we found
  console.log('Extracted data:', data);

  // Encode data and open localhost page (avoids HTTPS->HTTP fetch blocking)
  const encoded = encodeURIComponent(JSON.stringify(data));
  window.open('http://localhost:8777/enrich-submit.html?data=' + encoded, '_blank', 'width=500,height=400');
})();
