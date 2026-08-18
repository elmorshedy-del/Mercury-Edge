import { PaperTradingControl } from "@/components/PaperTradingControl";
import { PaperTradingTerminal } from "@/components/PaperTradingTerminal";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

export default function PaperTraderPage() {
  return (
    <div className={styles.page}>
      <PaperTradingTerminal />

      <section className={styles.settings} id="paper-settings">
        <div className={styles.settingsHeader}>
          <div>
            <span>Configuration</span>
            <h2>Paper engine settings</h2>
          </div>
          <p>
            Existing controls are preserved below the terminal. This section changes presentation only; execution logic and trading rules remain in their original components and services.
          </p>
        </div>
        <PaperTradingControl />
      </section>
    </div>
  );
}
