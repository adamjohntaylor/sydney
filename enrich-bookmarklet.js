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

    // Cover image - try og:image first, then gallery
    const ogImage = document.querySelector('meta[property="og:image"]');
    if (ogImage) {
      data.cover_image = ogImage.content;
    } else {
      const galleryImg = document.querySelector('[data-testid="listing-details__gallery"] img, .listing-details__gallery img, picture img');
      if (galleryImg) data.cover_image = galleryImg.src;
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

    // Price
    const price = document.querySelector('[data-testid="listing-details__summary-title-price"], .listing-details__summary-title-price, [class*="price"]');
    if (price) {
      data.price_guide_text = price.textContent.trim();
    }

    // Description
    const desc = document.querySelector('[data-testid="listing-details__description"], .listing-details__description');
    if (desc) {
      data.description = desc.textContent.trim().substring(0, 500);
    }

  } else if (isREA) {
    // Extract from REA listing page

    // Cover image
    const ogImage = document.querySelector('meta[property="og:image"]');
    if (ogImage) {
      data.cover_image = ogImage.content;
    } else {
      const heroImg = document.querySelector('[class*="hero"] img, [class*="gallery"] img, [class*="carousel"] img');
      if (heroImg) data.cover_image = heroImg.src;
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

    // Price
    const priceEl = document.querySelector('[class*="price"], [class*="Price"]');
    if (priceEl) {
      const priceText = priceEl.textContent.trim();
      if (priceText.includes('$') || priceText.toLowerCase().includes('guide') || priceText.toLowerCase().includes('contact')) {
        data.price_guide_text = priceText;
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

  // Send to local server
  fetch('http://localhost:8777/api/enrich-listing', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  .then(r => r.json())
  .then(result => {
    if (result.ok) {
      alert('Saved! Matched: ' + result.matched + '\n\nEnriched: ' + result.enriched.join(', '));
    } else {
      alert('Error: ' + result.error + '\n\nExtracted address: ' + (data.address || 'none') + ', ' + (data.suburb || 'none'));
    }
  })
  .catch(err => {
    alert('Error connecting to local server.\n\nMake sure python scripts/serve.py is running.\n\n' + err.message);
  });
})();
