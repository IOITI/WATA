// The address of Vela-pinets' worker, served as a file by admin_ui/vite/velaPineWorker.ts.
declare module 'virtual:vela-pine-worker' {
  const url: string
  export default url
}
