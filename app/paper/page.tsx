import { LiveMonitor } from "@/components/LiveMonitor";
import { PaperTradingControl } from "@/components/PaperTradingControl";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

export default function PaperTraderPage() {
  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <a className={styles.brand} href="/">
          <span className={styles.mark} />
          <span className={styles.brandText}>
            <small>Mercury Edge</small>
            <strong>Paper Trader</strong>
          </span>
        </a>
        <a className={styles.back} href="/">← Research dashboard</a>
      </header>

      <section className={styles.hero}>
        <div>
          <div className={styles.eyebrow}><span className={styles.dot} />Live paper execution</div>
          <h1>The bot.<br /><em>One signal stream.</em><br />Six $1,000 portfolios.</h1>
          <p>
            This is the operating dashboard for Mercury Edge paper trading. Every portfolio sees the same audited weather and Kalshi L2 timeline; strategies and capital rules can be compared without splitting bankroll between modes.
          </p>
        </div>
        <div className={styles.modePill}>Real money disabled</div>
      </section>

      <div className={styles.rule} />
      <PaperTradingControl />

      <section className={styles.live}>
        <div className={styles.liveTitle}>Live ingestion monitor</div>
        <LiveMonitor />
      </section>
    </div>
  );
}
