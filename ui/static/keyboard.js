/*
  On-screen keyboard, built into the page rather than relying on the OS.
  See keyboard.css for why this exists at all.

  Attaches via event delegation on document (focusin/focusout), so any
  text input on any screen gets this automatically -- nothing needs to
  opt in, and it keeps working if more text fields get added later.
*/

const LETTER_ROWS = [
  ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
  ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
  ["\u21e7", "z", "x", "c", "v", "b", "n", "m", "\u232b"], // shift, letters, backspace
];

const NUMBER_ROWS = [
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
  ["-", "'", '"', ":", ";", "(", ")", "/", "@"],
  [".", ",", "?", "!", "\u232b"], // backspace
];

let activeInput = null;
let shiftOn = false;
let numberMode = false;
let kbEl = null;

function isTextInput(el) {
  return el && el.tagName === "INPUT" && (el.type === "text" || el.type === "search" || !el.type);
}

function buildKeyboard() {
  const el = document.createElement("div");
  el.id = "rc-keyboard";
  render(el);
  document.body.appendChild(el);
  return el;
}

function render(el) {
  const rows = numberMode ? NUMBER_ROWS : LETTER_ROWS;
  el.innerHTML = "";

  rows.forEach((row) => {
    const rowEl = document.createElement("div");
    rowEl.className = "rc-kb-row";
    row.forEach((label) => {
      const key = document.createElement("button");
      key.type = "button";
      key.className = "rc-kb-key";
      let display = label;
      if (!numberMode && label.length === 1 && /[a-z]/i.test(label)) {
        display = shiftOn ? label.toUpperCase() : label;
      }
      if (label === "\u21e7") {
        key.classList.add("rc-kb-key-shift");
        if (shiftOn) key.classList.add("rc-kb-active");
      }
      if (label === "\u232b") key.classList.add("rc-kb-key-backspace");
      key.textContent = display;
      key.dataset.key = label;
      rowEl.appendChild(key);
    });
    el.appendChild(rowEl);
  });

  const bottomRow = document.createElement("div");
  bottomRow.className = "rc-kb-row";

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "rc-kb-key rc-kb-key-wide";
  toggle.textContent = numberMode ? "ABC" : "123";
  toggle.dataset.action = "toggle-numbers";
  bottomRow.appendChild(toggle);

  const space = document.createElement("button");
  space.type = "button";
  space.className = "rc-kb-key rc-kb-key-space";
  space.textContent = " ";
  space.dataset.key = " ";
  bottomRow.appendChild(space);

  const done = document.createElement("button");
  done.type = "button";
  done.className = "rc-kb-key rc-kb-key-done";
  done.textContent = "Done";
  done.dataset.action = "done";
  bottomRow.appendChild(done);

  el.appendChild(bottomRow);
}

function insertAtCursor(input, text) {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  const before = input.value.slice(0, start);
  const after = input.value.slice(end);

  // maxlength is a native browser restriction on typed/pasted input --
  // setting .value directly (which is how this keyboard has to work,
  // there's no real keystroke to simulate) bypasses it entirely, so it
  // has to be enforced here explicitly or these fields' 40/60 char
  // limits would silently do nothing when typed via this keyboard.
  const max = input.maxLength;
  if (max != null && max >= 0) {
    const roomLeft = max - (before.length + after.length);
    if (roomLeft <= 0) return;
    if (text.length > roomLeft) text = text.slice(0, roomLeft);
  }

  input.value = before + text + after;
  const newPos = start + text.length;
  input.setSelectionRange(newPos, newPos);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function backspaceAtCursor(input) {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? input.value.length;
  if (start === end) {
    if (start === 0) return;
    input.value = input.value.slice(0, start - 1) + input.value.slice(start);
    input.setSelectionRange(start - 1, start - 1);
  } else {
    input.value = input.value.slice(0, start) + input.value.slice(end);
    input.setSelectionRange(start, start);
  }
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function handleKeyTap(e) {
  // Stops the input from ever blurring when a keyboard key is tapped --
  // without this, tapping any element other than the input itself would
  // shift focus away and close the keyboard on every single keystroke.
  e.preventDefault();

  const btn = e.target.closest("button");
  if (!btn || !activeInput) return;

  const action = btn.dataset.action;
  if (action === "toggle-numbers") {
    numberMode = !numberMode;
    render(kbEl);
    return;
  }
  if (action === "done") {
    hideKeyboard();
    return;
  }

  const key = btn.dataset.key;
  if (key === "\u232b") {
    backspaceAtCursor(activeInput);
    return;
  }
  if (key === "\u21e7") {
    shiftOn = !shiftOn;
    render(kbEl);
    return;
  }

  let char = key;
  if (!numberMode && shiftOn && /[a-z]/i.test(char)) {
    char = char.toUpperCase();
    shiftOn = false;
    render(kbEl);
  }
  insertAtCursor(activeInput, char);
}

function showKeyboard(input) {
  activeInput = input;
  if (!kbEl) {
    kbEl = buildKeyboard();
    kbEl.addEventListener("pointerdown", handleKeyTap);
  }
  document.body.classList.add("rc-keyboard-open");
  kbEl.classList.add("rc-keyboard-visible");
  // Give the layout a moment to settle (padding-bottom transition) before
  // scrolling, so the field lands correctly above the keyboard rather
  // than wherever it was before the page grew.
  setTimeout(() => {
    input.scrollIntoView({ block: "center", behavior: "smooth" });
  }, 50);
}

function hideKeyboard() {
  if (kbEl) kbEl.classList.remove("rc-keyboard-visible");
  document.body.classList.remove("rc-keyboard-open");
  activeInput = null;
  shiftOn = false;
  numberMode = false;
}

document.addEventListener("focusin", (e) => {
  if (isTextInput(e.target)) {
    showKeyboard(e.target);
  }
});

document.addEventListener("focusout", (e) => {
  // Only close if focus isn't moving to another text input (e.g. tabbing
  // between fields) and isn't landing on the keyboard itself (which
  // already can't steal focus, but this guards any edge case).
  setTimeout(() => {
    const stillRelevant = isTextInput(document.activeElement) || (kbEl && kbEl.contains(document.activeElement));
    if (!stillRelevant) hideKeyboard();
  }, 0);
});
