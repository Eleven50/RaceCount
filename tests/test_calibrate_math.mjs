// Two things tested here:
//  1. calibrate.js's own screen-tap -> image-pixel coordinate math
//     (mirrored below, since there's no real browser in this sandbox to
//     load the actual page in).
//  2. An independent JS reimplementation of the segment-intersection
//     algorithm used server-side in logic/zones.py, to verify the
//     algorithm itself (not any specific file) behaves correctly across
//     the tricky cases — direction-agnostic, finite vs. infinite line,
//     parallel segments. calibrate.js does NOT contain this logic
//     (crossing detection is server-side only); this is a correctness
//     cross-check of the math, not a test of shipped client-side code.
//
// Keep in sync with the real calibrate.js and logic/zones.py if either
// changes.

function computeImageBox(wrapRect, naturalW, naturalH) {
  const wrapAspect = wrapRect.width / wrapRect.height;
  const naturalAspect = naturalW / naturalH;

  let renderedW, renderedH, offsetX, offsetY;
  if (naturalAspect > wrapAspect) {
    renderedW = wrapRect.width;
    renderedH = wrapRect.width / naturalAspect;
    offsetX = 0;
    offsetY = (wrapRect.height - renderedH) / 2;
  } else {
    renderedH = wrapRect.height;
    renderedW = wrapRect.height * naturalAspect;
    offsetY = 0;
    offsetX = (wrapRect.width - renderedW) / 2;
  }
  return { offsetX, offsetY, renderedW, renderedH, naturalW, naturalH };
}

function screenToImageCoords(clientX, clientY, wrapRect, imageBox) {
  const localX = clientX - wrapRect.left - imageBox.offsetX;
  const localY = clientY - wrapRect.top - imageBox.offsetY;
  if (localX < 0 || localY < 0 || localX > imageBox.renderedW || localY > imageBox.renderedH) {
    return null;
  }
  return {
    x: Math.round((localX / imageBox.renderedW) * imageBox.naturalW),
    y: Math.round((localY / imageBox.renderedH) * imageBox.naturalH),
  };
}

function orientation(a, b, c) {
  return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function segmentsIntersect(a, b, c, d) {
  const d1 = orientation(c, d, a);
  const d2 = orientation(c, d, b);
  const d3 = orientation(a, b, c);
  const d4 = orientation(a, b, d);
  return (d1 > 0) !== (d2 > 0) && (d3 > 0) !== (d4 > 0) && d1 !== 0 && d2 !== 0 && d3 !== 0 && d4 !== 0;
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exitCode = 1;
  } else {
    console.log("PASS:", msg);
  }
}

function approx(a, b, tol = 0.6) {
  return Math.abs(a - b) <= tol;
}

// ---------- Test 1: exact aspect match, no letterboxing ----------
// natural 960x720 (4:3), wrapper also 4:3 at 480x360 -> should map 1:2 exactly, no offset
{
  const wrapRect = { left: 100, top: 50, width: 480, height: 360 };
  const box = computeImageBox(wrapRect, 960, 720);
  assert(approx(box.offsetX, 0) && approx(box.offsetY, 0), "no-letterbox case has zero offset");
  assert(approx(box.renderedW, 480) && approx(box.renderedH, 360), "no-letterbox case fills wrapper exactly");

  // tap at the exact center of the displayed image
  const centerScreen = { x: wrapRect.left + 240, y: wrapRect.top + 180 };
  const coords = screenToImageCoords(centerScreen.x, centerScreen.y, wrapRect, box);
  assert(approx(coords.x, 480) && approx(coords.y, 360), `center tap maps to natural center (got ${coords.x},${coords.y})`);

  // tap at top-left corner of the displayed image
  const corner = screenToImageCoords(wrapRect.left, wrapRect.top, wrapRect, box);
  assert(approx(corner.x, 0) && approx(corner.y, 0), `top-left tap maps to (0,0) (got ${corner.x},${corner.y})`);
}

// ---------- Test 2: wide wrapper, natural image is 4:3 -> letterboxed top/bottom ----------
{
  const wrapRect = { left: 0, top: 0, width: 1000, height: 400 }; // very wide, 2.5:1
  const box = computeImageBox(wrapRect, 960, 720); // natural aspect 1.333
  // wrapAspect(2.5) > naturalAspect(1.333) -> full height used, letterboxed left/right per the code's branch logic
  assert(approx(box.offsetY, 0), "wide wrapper: no vertical letterbox");
  assert(box.offsetX > 0, "wide wrapper: has horizontal letterbox (pillarbox)");
  assert(approx(box.renderedH, 400), "wide wrapper: rendered height fills wrapper height");
  const expectedRenderedW = 400 * (960 / 720);
  assert(approx(box.renderedW, expectedRenderedW), `wide wrapper: rendered width = ${expectedRenderedW} (got ${box.renderedW})`);

  // Tap exactly at the vertical center-left edge of the actual rendered image (not the wrapper edge)
  const imgLeftEdge = { x: box.offsetX, y: wrapRect.height / 2 };
  const coords = screenToImageCoords(imgLeftEdge.x, imgLeftEdge.y, wrapRect, box);
  assert(coords !== null && approx(coords.x, 0), `tap at rendered-image left edge maps to natural x=0 (got ${coords && coords.x})`);

  // Tap inside the letterbox padding (x=5, near wrapper's true left edge, left of the rendered image) should be rejected
  const inPadding = screenToImageCoords(2, 200, wrapRect, box);
  assert(inPadding === null, "tap inside horizontal letterbox padding is correctly rejected");
}

// ---------- Test 3: tall wrapper, natural image is 4:3 -> letterboxed top/bottom ----------
{
  const wrapRect = { left: 0, top: 0, width: 400, height: 1000 }; // very tall, 0.4:1
  const box = computeImageBox(wrapRect, 960, 720);
  assert(approx(box.offsetX, 0), "tall wrapper: no horizontal letterbox");
  assert(box.offsetY > 0, "tall wrapper: has vertical letterbox");

  const inTopPadding = screenToImageCoords(200, 2, wrapRect, box);
  assert(inTopPadding === null, "tap inside vertical letterbox padding is correctly rejected");
}

// ---------- Test 4: segment-intersection matches the Python implementation ----------
// Mirrors logic/zones.py's _segments_intersect test cases exactly.
{
  assert(segmentsIntersect([50, 100], [150, 100], [100, 0], [100, 200]) === true,
    "perpendicular crossing through middle detected");
  assert(segmentsIntersect([50, 100], [90, 100], [100, 0], [100, 200]) === false,
    "movement stopping short of the line correctly not detected");
  assert(segmentsIntersect([50, 500], [150, 500], [100, 0], [100, 200]) === false,
    "crossing the infinite line extension but missing the finite segment correctly rejected");
  assert(segmentsIntersect([150, 100], [50, 100], [100, 0], [100, 200]) === true,
    "crossing detected regardless of movement direction");
  assert(segmentsIntersect([500, 0], [500, 100], [400, 50], [600, 50]) === true,
    "horizontal gate line crossed by vertical movement (matches Gate B)");
  assert(segmentsIntersect([0, 0], [0, 100], [50, 0], [50, 100]) === false,
    "parallel segments never intersect");
}

console.log(process.exitCode ? "\nSOME TESTS FAILED" : "\nALL COORDINATE MATH TESTS PASSED");
