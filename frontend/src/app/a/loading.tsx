export default function Loading() {
  return (
    <div className="w-full min-h-screen">
      <div className="mx-auto max-w-[1840px] px-4 py-8">
        <div className="animate-pulse space-y-4">
          <div className="h-7 w-64 rounded bg-bg-tertiary" />
          <div className="h-4 w-96 rounded bg-bg-tertiary/60" />
          <div className="grid gap-3 sm:grid-cols-4 mt-6">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="cockpit-card h-[88px]" />
            ))}
          </div>
          <div className="grid gap-3 2xl:grid-cols-2 mt-3">
            <div className="cockpit-card h-[320px]" />
            <div className="cockpit-card h-[320px]" />
          </div>
        </div>
      </div>
    </div>
  );
}
