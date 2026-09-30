"use client";

import styles from "@/components/WeatherStudyTable.module.css";

const rows = [
  { city: "NYC", sep27: "64 → 63 (+1)", sep28: "62 → 62 (0)", sep29: "72 → 70 (+2)", mae: "1.0°F", hits: "2/3" },
  { city: "Philadelphia", sep27: "66 → 67 (-1)", sep28: "68 → 69 (-1)", sep29: "76 → 74 (+2)", mae: "1.33°F", hits: "0/3" },
  { city: "LAX", sep27: "86 → 85 (+1)", sep28: "77 → 77 (0)", sep29: "79 → 83 (-4)", mae: "1.67°F", hits: "2/3" },
  { city: "Denver", sep27: "84 → 83 (+1)", sep28: "70 → 71 (-1)", sep29: "71 → 71 (0)", mae: "0.67°F", hits: "2/3" },
  { city: "Seattle", sep27: "66 → 65 (+1)", sep28: "66 → 64 (+2)", sep29: "64 → 63 (+1)", mae: "1.33°F", hits: "1/3" },
];

export function WeatherStudyTable() {
  return (
    <section className={styles.study}>
      <header className={styles.header}>
        <div>
          <span>Calibration study</span>
          <h2>TWC forecast high vs realized daily high</h2>
          <p>Sep 27–29 · frozen forecast on the left, realized high on the right, forecast error in parentheses.</p>
        </div>
        <div className={styles.legend}><b>MAE</b><span>Mean absolute error</span></div>
      </header>

      <div className={styles.tableWrap}>
        <table>
          <thead>
            <tr>
              <th>City</th>
              <th>Sep 27</th>
              <th>Sep 28</th>
              <th>Sep 29</th>
              <th>3-day MAE</th>
              <th>Winning-bucket hits</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.city}>
                <td><b>{row.city}</b></td>
                <td>{row.sep27}</td>
                <td>{row.sep28}</td>
                <td>{row.sep29}</td>
                <td className={styles.mae}>{row.mae}</td>
                <td><span className={styles.hit}>{row.hits}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className={styles.note}>Error sign = TWC forecast high − realized high. “Winning-bucket hit” means the frozen TWC high landed inside the Kalshi bucket that ultimately settled YES; it is stricter than simply being close in degrees.</p>
    </section>
  );
}
