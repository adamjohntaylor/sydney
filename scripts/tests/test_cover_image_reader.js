// node scripts/tests/test_cover_image_reader.js   (needs: npm i jsdom)
// Exercises readCoverImageFromPage() from extension/content.js against the
// page shapes it is built for: og:image, JSON-LD image, id-scoped page data
// (a neighbouring listing's photo must NOT be taken), gallery <img> srcset,
// and a page with only a logo and a static map (no photo).
const { JSDOM } = require('jsdom'); const fs = require('fs'); const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'extension', 'content.js'), 'utf8');
const fn = src.slice(src.indexOf('function readCoverImageFromPage'), src.indexOf('// ---- Outgoings reader'));
function run(html, url) { const w = new JSDOM(html, { url }).window; return new Function('document', 'location', fn + '; return readCoverImageFromPage();')(w.document, w.location); }
const LID = '2021000000', U = 'https://www.domain.com.au/1-a-st-balmain-nsw-2041-' + LID;
const cases = [
  ['og:image (Domain)', `<html><head><meta property="og:image" content="https://rimh2.domainstatic.com.au/x=/fit-in/1920x1080/2021000000_1_1_251001-w5000-h3000"></head><body></body></html>`, U, 'og:image'],
  ['og:image is a logo -> JSON-LD image', `<html><head><meta property="og:image" content="https://rimh2.domainstatic.com.au/agency-logo.png"><script type="application/ld+json">{"@graph":[{"@type":"Residence","image":[{"@type":"ImageObject","url":"https://rimh2.domainstatic.com.au/p/2021000000_2_1-w1.jpg"}]}]}</script></head><body></body></html>`, U, 'json-ld image'],
  ['page data, id-scoped (neighbour listing first, rejected)', `<html><body><script>var a={"id":2020999999,"images":[{"url":"https:\\/\\/rimh2.domainstatic.com.au\\/neighbour-w1"}]}; ${'x'.repeat(3000)}; var b={"listingId":2021000000,"images":[{"url":"https:\\/\\/rimh2.domainstatic.com.au\\/mine-w1"}]};</script></body></html>`, U, 'page data'],
  ['REA gallery <img> srcset (largest candidate)', `<html><body><div class="Gallery__x"><img src="https://i2.au.reastatic.net/300x200/abc/image.jpg" srcset="https://i2.au.reastatic.net/300x200/abc/image.jpg 300w, https://i2.au.reastatic.net/1200x800/abc/image.jpg 1200w"></div></body></html>`, 'https://www.realestate.com.au/property-apartment-nsw-balmain-' + LID, 'gallery img'],
  ['logo + static map only -> none', `<html><head><meta property="og:image" content="https://www.domain.com.au/static/logo.png"></head><body><img src="https://maps.googleapis.com/staticmap"></body></html>`, U, 'no property photo found'],
];
let ok = 0;
for (const [name, html, url, basis] of cases) {
  const r = run(html, url);
  const pass = r.image_basis === basis && r.image_checked === true && (basis === 'no property photo found' ? !r.cover_image : !!r.cover_image);
  if (pass) ok++; console.log((pass ? 'PASS ' : 'FAIL ') + name + '  ' + JSON.stringify(r));
}
console.log(ok + '/' + cases.length); process.exit(ok === cases.length ? 0 : 1);
