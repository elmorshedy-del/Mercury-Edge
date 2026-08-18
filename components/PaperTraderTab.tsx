import styles from "./PaperTraderTab.module.css";

export function PaperTraderTab() {
  return (
    <a className={styles.tab} href="/paper" aria-label="Open Paper Trader dashboard">
      <span className={styles.pulse} />
      <span className={styles.copy}>
        <small>Live paper system</small>
        <strong>Paper Trader</strong>
      </span>
      <span className={styles.arrow}>↗</span>
    </a>
  );
}
