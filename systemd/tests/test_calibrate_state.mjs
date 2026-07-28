// Mirrors the pure state-transition logic from calibrate.js (addPoint,
// confirmActiveGate, undoLastPoint, restartAll, and the derived-state
// helpers) without the DOM-touching parts, so it can run standalone.
// Keep in sync with calibrate.js if that state machine changes.

const GATE_SEQUENCE = ["left", "straight", "right"];

function makeState() {
  return { gatePoints: { left: [], straight: [], right: [] }, confirmedGateCount: 0 };
}

function isAllConfirmed(s) {
  return s.confirmedGateCount >= GATE_SEQUENCE.length;
}
function activeGate(s) {
  return isAllConfirmed(s) ? null : GATE_SEQUENCE[s.confirmedGateCount];
}
function readyToConfirmActiveGate(s) {
  const gate = activeGate(s);
  return gate !== null && s.gatePoints[gate].length === 2;
}
function isLastGate(s) {
  return s.confirmedGateCount === GATE_SEQUENCE.length - 1;
}
function totalPointsTapped(s) {
  return GATE_SEQUENCE.reduce((sum, g) => sum + s.gatePoints[g].length, 0);
}

function addPoint(s, coords) {
  if (isAllConfirmed(s)) return;
  const gate = activeGate(s);
  if (s.gatePoints[gate].length >= 2) return;
  s.gatePoints[gate].push(coords);
}

function confirmActiveGate(s) {
  if (!readyToConfirmActiveGate(s)) return;
  s.confirmedGateCount += 1;
}

function undoLastPoint(s) {
  if (!isAllConfirmed(s)) {
    const gate = activeGate(s);
    if (s.gatePoints[gate].length > 0) {
      s.gatePoints[gate].pop();
      return;
    }
  }
  if (s.confirmedGateCount > 0) {
    s.confirmedGateCount -= 1;
    const gate = GATE_SEQUENCE[s.confirmedGateCount];
    s.gatePoints[gate].pop();
  }
}

function restartAll(s) {
  s.gatePoints = { left: [], straight: [], right: [] };
  s.confirmedGateCount = 0;
}

// ---------- Assertions ----------

let failures = 0;
function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    failures++;
  } else {
    console.log("PASS:", msg);
  }
}

// ---------- Test 1: full happy path through all 3 gates ----------
{
  const s = makeState();
  assert(activeGate(s) === "left", "starts on Gate A (left)");

  addPoint(s, [10, 10]);
  assert(!readyToConfirmActiveGate(s), "not ready after only 1 point");
  addPoint(s, [10, 50]);
  assert(readyToConfirmActiveGate(s), "ready after 2 points");
  assert(!isLastGate(s), "left is not the last gate");

  confirmActiveGate(s);
  assert(activeGate(s) === "straight", "advances to Gate B (straight) after confirm");
  assert(s.gatePoints.left.length === 2, "Gate A's points remain after confirming");

  addPoint(s, [100, 10]);
  addPoint(s, [200, 10]);
  confirmActiveGate(s);
  assert(activeGate(s) === "right", "advances to Gate C (right)");
  assert(isLastGate(s), "right is the last gate");

  addPoint(s, [400, 10]);
  addPoint(s, [400, 50]);
  assert(readyToConfirmActiveGate(s), "Gate C ready to confirm");
  confirmActiveGate(s);
  assert(isAllConfirmed(s), "all 3 gates confirmed after Gate C");
  assert(activeGate(s) === null, "no active gate once all confirmed");
}

// ---------- Test 2: taps beyond 2 points for a gate are ignored (must confirm first) ----------
{
  const s = makeState();
  addPoint(s, [1, 1]);
  addPoint(s, [2, 2]);
  addPoint(s, [3, 3]); // should be ignored — already has 2 points, awaiting confirm
  assert(s.gatePoints.left.length === 2, "3rd tap on same gate ignored before confirmation");
}

// ---------- Test 3: taps while reviewing (all confirmed) are ignored ----------
{
  const s = makeState();
  for (const gate of GATE_SEQUENCE) {
    addPoint(s, [1, 1]);
    addPoint(s, [2, 2]);
    confirmActiveGate(s);
  }
  assert(isAllConfirmed(s), "setup: all confirmed");
  addPoint(s, [99, 99]); // should be a no-op
  assert(s.gatePoints.right.length === 2, "tap while reviewing does not mutate a confirmed gate");
}

// ---------- Test 4: undo mid-gate (1 point tapped) ----------
{
  const s = makeState();
  addPoint(s, [1, 1]);
  undoLastPoint(s);
  assert(s.gatePoints.left.length === 0, "undo removes the single tapped point");
  assert(activeGate(s) === "left", "still on Gate A after undoing its only point");
}

// ---------- Test 5: undo the 2nd point (back to awaiting Point B, not yet confirmed) ----------
{
  const s = makeState();
  addPoint(s, [1, 1]);
  addPoint(s, [2, 2]);
  assert(readyToConfirmActiveGate(s), "setup: ready to confirm");
  undoLastPoint(s);
  assert(s.gatePoints.left.length === 1, "undo drops back to 1 point");
  assert(!readyToConfirmActiveGate(s), "no longer ready to confirm after undo");
}

// ---------- Test 6: undo across a gate boundary (already confirmed previous gate) ----------
{
  const s = makeState();
  addPoint(s, [1, 1]);
  addPoint(s, [2, 2]);
  confirmActiveGate(s); // Gate A confirmed, now on Gate B with 0 points
  assert(activeGate(s) === "straight", "setup: now on Gate B");

  undoLastPoint(s); // Gate B has 0 points -> should step back into Gate A and pop its last point
  assert(activeGate(s) === "left", "undo steps back into Gate A when Gate B has no points yet");
  assert(s.gatePoints.left.length === 1, "Gate A's last point was popped");
  assert(!isAllConfirmed(s), "no longer all-confirmed (was never all-confirmed here, sanity check)");
}

// ---------- Test 7: undo from the review state (all 3 confirmed) steps back into Gate C ----------
{
  const s = makeState();
  for (const gate of GATE_SEQUENCE) {
    addPoint(s, [1, 1]);
    addPoint(s, [2, 2]);
    confirmActiveGate(s);
  }
  assert(isAllConfirmed(s), "setup: all confirmed / reviewing");

  undoLastPoint(s);
  assert(!isAllConfirmed(s), "undo from review exits the all-confirmed state");
  assert(activeGate(s) === "right", "back on Gate C after undo from review");
  assert(s.gatePoints.right.length === 1, "Gate C dropped from 2 points to 1");
  assert(!readyToConfirmActiveGate(s), "Gate C no longer ready to confirm");
}

// ---------- Test 8: repeated undo fully unwinds back to the very start ----------
{
  const s = makeState();
  for (const gate of GATE_SEQUENCE) {
    addPoint(s, [1, 1]);
    addPoint(s, [2, 2]);
    confirmActiveGate(s);
  }
  let guard = 0;
  while (totalPointsTapped(s) > 0 && guard < 20) {
    undoLastPoint(s);
    guard++;
  }
  assert(totalPointsTapped(s) === 0, `fully unwound to zero points (took ${guard} undos)`);
  assert(s.confirmedGateCount === 0, "confirmedGateCount back to 0");
  assert(activeGate(s) === "left", "back to Gate A as the active gate");
  assert(guard === 6, `exactly 6 undos needed to unwind 6 points (took ${guard})`);
}

// ---------- Test 9: restart clears everything regardless of state ----------
{
  const s = makeState();
  for (const gate of GATE_SEQUENCE) {
    addPoint(s, [1, 1]);
    addPoint(s, [2, 2]);
    confirmActiveGate(s);
  }
  restartAll(s);
  assert(totalPointsTapped(s) === 0, "restart clears all points");
  assert(s.confirmedGateCount === 0, "restart resets confirmedGateCount");
  assert(activeGate(s) === "left", "restart returns to Gate A");
}

// ---------- Test 10: confirming without 2 points is a no-op (defensive) ----------
{
  const s = makeState();
  addPoint(s, [1, 1]);
  confirmActiveGate(s); // only 1 point — should not advance
  assert(s.confirmedGateCount === 0, "confirm with only 1 point tapped does nothing");
  assert(activeGate(s) === "left", "still on Gate A");
}

console.log(failures ? `\n${failures} TEST(S) FAILED` : "\nALL STATE MACHINE TESTS PASSED");
process.exitCode = failures ? 1 : 0;
