// Regression guards for Windows `hercules` resolution in main.ts.
//
// main.ts has no module.exports, so these follow the repo's source-assertion
// test pattern (see windows-child-process.test.ts). They pin the two Windows
// resolution bugs that caused desktop reinstall loops:
//   1. findOnPath() tried the empty extension FIRST, so an extensionless
//      Git-Bash `hercules` shim shadowed the real hercules.cmd/hercules.exe; the
//      shim then failed the --version probe and the desktop fell through to a
//      spurious bootstrap/repair.
//   2. handOffWindowsBootstrapRecovery() chose --update vs the destructive
//      --repair by checking ONLY venv\Scripts\hercules.exe (the console-script
//      shim, written at the END of venv setup and absent in interrupted
//      states), so it escalated to a full venv recreate even on healthy
//      installs.
//   3. unwrapWindowsVenvHerculesCommand() returned the venv python with NO
//      runtime probe (bypassing the caller's --version check too), so a venv
//      broken mid-update (e.g. missing python-dotenv) was re-selected forever:
//      Retry / "Repair install" resolved the same dead interpreter instead of
//      falling through to the bootstrap installer.

import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

function readMain() {
  return fs.readFileSync(path.join(__dirname, 'main.ts'), 'utf8').replace(/\r\n/g, '\n')
}

test('findOnPath tries PATHEXT extensions before the bare (empty) name on Windows', () => {
  const source = readMain()
  // Fixed order: PATHEXT first, empty string LAST.
  assert.match(
    source,
    /\(process\.env\.PATHEXT \|\| '\.COM;\.EXE;\.BAT;\.CMD'\)\.split\(';'\)\.filter\(Boolean\), ''\]/,
    'extensions array must end with the empty string, not start with it'
  )
  // The buggy empty-first order must not return.
  assert.doesNotMatch(
    source,
    /\['', \.\.\.\(process\.env\.PATHEXT/,
    'empty-extension-first order regressed: an extensionless shim can shadow hercules.cmd/.exe'
  )
})

test('Windows bootstrap recovery chooses --update when any real-install signal is present', () => {
  const source = readMain()
  assert.match(source, /const haveRealInstall =/, 'recovery must compute haveRealInstall')
  assert.match(source, /fileExists\(venvPython\)/, 'recovery must accept the venv interpreter as a real-install signal')
  assert.match(
    source,
    /\.hercules-bootstrap-complete/,
    'recovery must accept the bootstrap-complete marker as a real-install signal'
  )
  assert.match(source, /updaterArgs = haveRealInstall \? \['--update'/, 'updaterArgs must gate on haveRealInstall')
  // The old too-narrow check (only venv\Scripts\hercules.exe) must not return.
  assert.doesNotMatch(
    source,
    /updaterArgs = fileExists\(venvHercules\) \?/,
    'recovery regressed to gating only on the hercules.exe shim, which forces destructive --repair'
  )
})

test('unwrapWindowsVenvHerculesCommand smoke-tests the venv python before trusting it', () => {
  const source = readMain()
  const fnStart = source.indexOf('function unwrapWindowsVenvHerculesCommand(')
  assert.notEqual(fnStart, -1, 'unwrapWindowsVenvHerculesCommand must exist in main.ts')
  // Slice out just the function body (up to the next top-level function decl)
  const fnEnd = source.indexOf('\nfunction ', fnStart + 1)
  const body = source.slice(fnStart, fnEnd === -1 ? undefined : fnEnd)
  assert.match(
    body,
    /canImportHerculesCli\(python/,
    'unwrap must probe the venv interpreter; returning it unprobed re-selects a broken venv ' +
      'forever (Retry/Repair loop on a mid-update venv missing e.g. python-dotenv)'
  )
  assert.match(
    body,
    /return null\s*\n\s*\}\s*\n\s*return \{/,
    'a failed probe must fall through (return null) so the resolver reaches the bootstrap rung'
  )
})
