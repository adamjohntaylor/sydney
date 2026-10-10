// Reader test for readOutgoingsFromPage() in extension/content.js.
// Needs jsdom (npm i jsdom); run: node scripts/tests/test_outgoings_reader.js
const fs = require('fs'), path = require('path');
let JSDOM; try { ({ JSDOM } = require('jsdom')); } catch (e) { console.log('skip: jsdom not installed'); process.exit(0); }
const cs = fs.readFileSync(path.join(__dirname, '../../extension/content.js'), 'utf8');
const fn = cs.slice(cs.indexOf('function readOutgoingsFromPage()'), cs.indexOf('// Client-rendered pages'));
const run = (html, url) => { const d = new JSDOM(html, { url }); return new Function('document', 'location', fn + '\nreturn readOutgoingsFromPage();')(d.window.document, d.window.location); };
const URL = 'https://www.domain.com.au/5-1-x-street-marrickville-nsw-2204-2020123456';
const assert = (c, m) => { if (!c) { console.error('FAIL', m); process.exit(1); } };
// Embedded-only description, escaped $ and <br>; a neighbour block >20k chars away is ignored.
const A = `<html><body><script type="application/json">{"listingId":2020123456,"description":"Sunny two-bed.<br/>Close to station.\\n- Water \\u0024178 | Council $495 | Strata $1,293 p/q","pad":"${'x'.repeat(25000)}","other":{"id":2099999999,"description":"Next street. Strata $9,999 p/q"}}</script></body></html>`;
const a = run(A, URL);
assert(a.outgoings_text === '- Water $178 | Council $495 | Strata $1,293 p/q', 'embedded: ' + a.outgoings_text);
assert(a.outgoings_text_source === 'page data (embedded description)', 'source');
// Visible + embedded: deduplicated, embedded adds the line the visible copy lacked.
const body = 'Lovely apartment with balcony and lift. '.repeat(6);
const B = `<html><body><div data-testid="listing-details__description">${body}\nStrata Levies: $3,228 Quarterly approx</div><script>window.x={"id":"2020123456","description":"${body}\\nStrata Levies: $3,228 Quarterly approx\\nCouncil Rates: $275 Quarterly approx"}</script></body></html>`;
const b = run(B, URL);
assert(b.outgoings_text === 'Strata Levies: $3,228 Quarterly approx \n\n Council Rates: $275 Quarterly approx', 'merge: ' + b.outgoings_text);
console.log('reader tests passed');
