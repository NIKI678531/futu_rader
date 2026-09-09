import { realpathSync } from 'node:fs'

/* True when the checkout sits on a mapped network drive. On a DFS/SMB mount
   `realpath.native` resolves the drive letter to the UNC share behind it, which is both
   the signal we test for and the cause of the two settings it guards:

   - `resolve.preserveSymlinks` — Vite would normalise the UNC path realpath hands back
     (`\\DFSHK01\PROJECT\…`) into a drive-relative one and then look for `P:\DFSHK01\…`,
     which does not exist. Preserving symlinks skips the realpath call. Nothing in this
     project is installed through a link, so it costs nothing.
   - `server.watch.usePolling` — SMB does not deliver change notifications, so the native
     watcher dies with `UNKNOWN (errno -4094)` and takes the dev server down with it.

   This lives apart from the two configs because both need it and neither should import
   the other: `vite.design.config.js` is rooted at `../design`, where the React plugin
   `vite.config.js` pulls in cannot be resolved. */
export const onNetworkDrive =
  realpathSync.native(import.meta.dirname) !== realpathSync(import.meta.dirname)

export const watch = onNetworkDrive ? { usePolling: true, interval: 400 } : undefined
