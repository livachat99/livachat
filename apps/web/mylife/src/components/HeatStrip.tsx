import type { OverviewDay } from '../api/types'

export default function HeatStrip({ days, onPick }: { days: OverviewDay[]; onPick: (date: string) => void }) {
  const total = days.reduce((acc, d) => acc + Object.values(d.counts).reduce((a, b) => a + b, 0), 0)
  if (total === 0) return null

  return (
    <div className="flex items-end gap-[2px] px-1">
      {days.map((d) => {
        const n = Object.values(d.counts).reduce((a, b) => a + b, 0)
        const h = Math.max(6, Math.round((n / total) * 48))
        return (
          <button
            key={d.date}
            onClick={() => onPick(d.date)}
            title={`${d.date} · ${n} 事件`}
            className="min-w-2 flex-1 rounded-sm bg-gold/25 transition-colors hover:bg-gold/60"
            style={{ height: h }}
          />
        )
      })}
    </div>
  )
}
