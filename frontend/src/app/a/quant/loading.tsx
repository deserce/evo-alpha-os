export default function Loading() {
  return (
    <main className="dashboard-page">
      <div className="dashboard-grid animate-pulse">
        <section className="dashboard-header dashboard-span-12">
          <div>
            <div className="h-3 w-24 rounded bg-bg-tertiary" />
            <div className="mt-2 h-7 w-48 rounded bg-bg-tertiary" />
          </div>
        </section>
        {[3, 3, 3, 3].map((span, i) => (
          <section key={i} className={`dashboard-panel dashboard-span-${span}`}>
            <div className="dashboard-panel-body h-[120px]" />
          </section>
        ))}
        <section className="dashboard-panel dashboard-span-5">
          <div className="dashboard-panel-body h-[200px]" />
        </section>
        <section className="dashboard-panel dashboard-span-4">
          <div className="dashboard-panel-body h-[200px]" />
        </section>
        <section className="dashboard-panel dashboard-span-3">
          <div className="dashboard-panel-body h-[200px]" />
        </section>
      </div>
    </main>
  );
}
