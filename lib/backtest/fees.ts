// Kalshi fees are contract- and account-dependent. This function implements a
// configurable quadratic approximation and is never presented as an exchange
// invoice. Set rate=0 to report gross results only.
export function estimateFee(price: number, contracts = 1, rate = 0.07) {
  if (price <= 0 || price >= 1 || contracts <= 0) return 0;
  return Math.ceil(rate * contracts * price * (1 - price) * 100) / 100;
}
