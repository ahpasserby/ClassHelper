/**
 * What this has cost, in the corner of the status bar.
 *
 * A token counter is not an answer to "can I afford to re-translate this
 * deck". Money is. It sits in the corner rather than in a page of its own
 * because the point is to notice it in passing, not to go and look it up --
 * and it stays quiet: one small number, with the arithmetic behind it a click
 * away for the times you do not believe it.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type Spend } from "./api";

/**
 * Rounded to the cent, except when that would round a real cost to nothing.
 *
 * A term's worth of reading comes to a few yuan, so most of the time two
 * decimals is the right amount of detail. On the first day it is fractions of
 * a cent, and three "<¥0.01" in a row tells the user less than the number
 * would.
 */
function yuan(value: number): string {
  if (value <= 0) return "¥0.00";
  if (value < 0.0005) return "<¥0.001";
  if (value < 1) return `¥${value.toFixed(3)}`;
  return `¥${value.toFixed(2)}`;
}

function when(ts: number | null | undefined): string {
  if (!ts) return "还没查过";
  const days = Math.floor((Date.now() / 1000 - ts) / 86400);
  if (days <= 0) return "今天更新";
  if (days === 1) return "昨天更新";
  return `${days} 天前更新`;
}

export function Spending({ activity }: { activity: number }) {
  const [spend, setSpend] = useState<Spend | null>(null);
  const [open, setOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [failed, setFailed] = useState("");

  const load = useCallback(async () => {
    try {
      setSpend(await api.spend());
    } catch {
      /* The ledger is a convenience; a reader that cannot show it still reads. */
    }
  }, []);

  // Translating a deck is hundreds of calls in a couple of minutes, so the
  // number has to move while you watch it -- but polling a local sqlite file
  // every second to watch it not move is silly. `activity` is the session's
  // call count, so a running translation refetches and an idle window does not.
  useEffect(() => {
    void load();
  }, [load, activity]);

  useEffect(() => {
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);

  const refresh = async () => {
    setRefreshing(true);
    setFailed("");
    try {
      setSpend(await api.refreshPrices());
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };

  if (!spend || (spend.calls === 0 && spend.total === 0)) return null;

  const unpriced = spend.unpriced_calls > 0;

  return (
    <span className="menu-anchor spend-anchor">
      <button
        className={`spend${unpriced ? " incomplete" : ""}`}
        title={unpriced ? "有调用还没有价格，总额只是下限" : "已花费（点开看明细）"}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(!open);
        }}
      >
        {yuan(spend.total)}
        {unpriced && <span className="spend-warn">+</span>}
      </button>

      {open && (
        <div className="menu spend-menu" onClick={(e) => e.stopPropagation()}>
          <div className="spend-rows">
            <div>
              <span>今天</span>
              <b>{yuan(spend.today)}</b>
            </div>
            <div>
              <span>本月</span>
              <b>{yuan(spend.month)}</b>
            </div>
            <div className="strong">
              <span>累计</span>
              <b>{yuan(spend.total)}</b>
            </div>
          </div>

          <div className="menu-rule" />

          <div className="spend-detail">
            {spend.by_model.slice(0, 4).map((row) => (
              <div key={`${row.model}:${row.kind}`}>
                <span className="name">{row.model}</span>
                <span className="muted">
                  {row.kind === "ask" ? "提问" : row.kind === "pricing" ? "查价" : "翻译"}
                  {" · "}
                  {row.calls} 次
                </span>
                <b>{yuan(row.cny)}</b>
              </div>
            ))}
          </div>

          {unpriced && (
            <>
              <div className="menu-rule" />
              <div className="spend-note">
                另有 {spend.unpriced_calls} 次调用还没有价格
                {spend.unpriced_models.length > 0 && (
                  <>（{spend.unpriced_models.join("、")}）</>
                )}
                ，没有计入上面的金额 —— 宁可少算，也不猜。
                {/* Some models a vendor still serves are not on its price
                    page at all. Nothing can be fetched for those, so say what
                    the user can actually do about it. */}
                <div className="muted">
                  服务商的价目页上查不到这个模型时，可以在{" "}
                  <code>用户配置/pricing.json</code> 里手填单价，改完立刻生效。
                </div>
              </div>
            </>
          )}

          <div className="menu-rule" />
          <div className="spend-note muted">
            价目取自服务商自己的价目页，{when(spend.pricing?.updated_at)}，每周自动核对一次。
            {spend.pricing?.note && <> {spend.pricing.note}</>}
          </div>
          {failed && <div className="spend-note error">{failed}</div>}
          <button className="link" disabled={refreshing} onClick={() => void refresh()}>
            {refreshing ? "正在查…" : "立即重新查价"}
          </button>
        </div>
      )}
    </span>
  );
}
