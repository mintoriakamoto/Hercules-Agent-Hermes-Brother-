import { contextBridge, ipcRenderer, webUtils } from 'electron'

contextBridge.exposeInMainWorld('herculesDesktop', {
  getConnection: profile => ipcRenderer.invoke('hercules:connection', profile),
  revalidateConnection: () => ipcRenderer.invoke('hercules:connection:revalidate'),
  touchBackend: profile => ipcRenderer.invoke('hercules:backend:touch', profile),
  getGatewayWsUrl: profile => ipcRenderer.invoke('hercules:gateway:ws-url', profile),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('hercules:window:openSession', sessionId, opts),
  openNewSessionWindow: () => ipcRenderer.invoke('hercules:window:openNewSession'),
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('hercules:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('hercules:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('hercules:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('hercules:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('hercules:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('hercules:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('hercules:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('hercules:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('hercules:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('hercules:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('hercules:pet-overlay:control', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('hercules:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('hercules:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('hercules:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('hercules:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('hercules:connection-config:test', payload),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('hercules:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('hercules:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('hercules:connection-config:oauth-logout', remoteUrl),
  // Hercules Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('hercules:cloud:status'),
    login: () => ipcRenderer.invoke('hercules:cloud:login'),
    logout: () => ipcRenderer.invoke('hercules:cloud:logout'),
    discover: org => ipcRenderer.invoke('hercules:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('hercules:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    get: () => ipcRenderer.invoke('hercules:profile:get'),
    set: name => ipcRenderer.invoke('hercules:profile:set', name)
  },
  api: request => ipcRenderer.invoke('hercules:api', request),
  notify: payload => ipcRenderer.invoke('hercules:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('hercules:requestMicrophoneAccess'),
  readFileDataUrl: filePath => ipcRenderer.invoke('hercules:readFileDataUrl', filePath),
  readFileText: filePath => ipcRenderer.invoke('hercules:readFileText', filePath),
  selectPaths: options => ipcRenderer.invoke('hercules:selectPaths', options),
  writeClipboard: text => ipcRenderer.invoke('hercules:writeClipboard', text),
  saveImageFromUrl: url => ipcRenderer.invoke('hercules:saveImageFromUrl', url),
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('hercules:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('hercules:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('hercules:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('hercules:watchPreviewFile', url),
  stopPreviewFileWatch: id => ipcRenderer.invoke('hercules:stopPreviewFileWatch', id),
  setTitleBarTheme: payload => ipcRenderer.send('hercules:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('hercules:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('hercules:translucency', payload),
  setPreviewShortcutActive: active => ipcRenderer.send('hercules:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('hercules:openExternal', url),
  openPreviewInBrowser: url => ipcRenderer.invoke('hercules:openPreviewInBrowser', url),
  fetchLinkTitle: url => ipcRenderer.invoke('hercules:fetchLinkTitle', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('hercules:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('hercules:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('hercules:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('hercules:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('hercules:zoom:get'),
    setPercent: percent => ipcRenderer.send('hercules:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('hercules:zoom:changed', listener)

      return () => ipcRenderer.removeListener('hercules:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('hercules:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('hercules:logs:recent'),
  readDir: dirPath => ipcRenderer.invoke('hercules:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('hercules:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('hercules:fs:reveal', targetPath),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('hercules:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('hercules:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('hercules:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('hercules:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('hercules:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('hercules:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('hercules:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('hercules:git:branchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('hercules:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('hercules:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('hercules:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('hercules:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('hercules:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('hercules:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('hercules:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('hercules:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('hercules:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('hercules:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('hercules:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('hercules:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('hercules:git:review:shipInfo', repoPath),
      createPr: repoPath => ipcRenderer.invoke('hercules:git:review:createPr', repoPath)
    }
  },
  terminal: {
    cwd: id => ipcRenderer.invoke('hercules:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('hercules:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('hercules:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('hercules:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('hercules:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `hercules:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `hercules:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('hercules:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('hercules:close-preview-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('hercules:open-updates', listener)

    return () => ipcRenderer.removeListener('hercules:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('hercules:deep-link', listener)

    return () => ipcRenderer.removeListener('hercules:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('hercules:deep-link-ready'),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('hercules:window-state-changed', listener)

    return () => ipcRenderer.removeListener('hercules:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('hercules:focus-session', listener)

    return () => ipcRenderer.removeListener('hercules:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('hercules:notification-action', listener)

    return () => ipcRenderer.removeListener('hercules:notification-action', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('hercules:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('hercules:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('hercules:backend-exit', listener)

    return () => ipcRenderer.removeListener('hercules:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('hercules:connection:applied', listener)

    return () => ipcRenderer.removeListener('hercules:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('hercules:power-resume', listener)

    return () => ipcRenderer.removeListener('hercules:power-resume', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('hercules:boot-progress', listener)

    return () => ipcRenderer.removeListener('hercules:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('hercules:bootstrap:get'),
  resetBootstrap: () => ipcRenderer.invoke('hercules:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('hercules:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('hercules:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('hercules:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('hercules:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('hercules:version'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('hercules:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('hercules:uninstall:summary'),
    run: mode => ipcRenderer.invoke('hercules:uninstall:run', { mode })
  },
  updates: {
    check: () => ipcRenderer.invoke('hercules:updates:check'),
    apply: opts => ipcRenderer.invoke('hercules:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('hercules:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('hercules:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('hercules:updates:progress', listener)

      return () => ipcRenderer.removeListener('hercules:updates:progress', listener)
    }
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('hercules:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('hercules:vscode-theme:search', query)
  }
})
