export type StationConfig = {
  city: string;
  station: string;
  timezone: string;
  kalshiSeries: string;
  nwsLocation?: string;
  iemNetwork?: string;
};

export const STATIONS: StationConfig[] = [
  { city: "New York", station: "KNYC", timezone: "America/New_York", kalshiSeries: "KXHIGHNY", nwsLocation: "NYC", iemNetwork: "NY_ASOS" },
  { city: "Philadelphia", station: "KPHL", timezone: "America/New_York", kalshiSeries: "KXHIGHPHIL", nwsLocation: "PHL", iemNetwork: "PA_ASOS" },
  { city: "Chicago", station: "KMDW", timezone: "America/Chicago", kalshiSeries: "KXHIGHCHI", nwsLocation: "MDW", iemNetwork: "IL_ASOS" },
  { city: "Denver", station: "KDEN", timezone: "America/Denver", kalshiSeries: "KXHIGHDEN", nwsLocation: "DEN", iemNetwork: "CO_ASOS" },
  { city: "Miami", station: "KMIA", timezone: "America/New_York", kalshiSeries: "KXHIGHMIA", nwsLocation: "MIA", iemNetwork: "FL_ASOS" },
  { city: "Los Angeles", station: "KLAX", timezone: "America/Los_Angeles", kalshiSeries: "KXHIGHLAX", nwsLocation: "LAX", iemNetwork: "CA_ASOS" },
  { city: "Austin", station: "KAUS", timezone: "America/Chicago", kalshiSeries: "KXHIGHAUS", nwsLocation: "AUS", iemNetwork: "TX_ASOS" },
  { city: "Atlanta", station: "KATL", timezone: "America/New_York", kalshiSeries: "KXHIGHTATL", nwsLocation: "ATL", iemNetwork: "GA_ASOS" },
  { city: "Boston", station: "KBOS", timezone: "America/New_York", kalshiSeries: "KXHIGHTBOS", nwsLocation: "BOS", iemNetwork: "MA_ASOS" },
  { city: "Dallas", station: "KDFW", timezone: "America/Chicago", kalshiSeries: "KXHIGHTDAL", nwsLocation: "DFW", iemNetwork: "TX_ASOS" },
  { city: "Washington", station: "KDCA", timezone: "America/New_York", kalshiSeries: "KXHIGHTDC", nwsLocation: "DCA", iemNetwork: "VA_ASOS" },
  { city: "Houston", station: "KHOU", timezone: "America/Chicago", kalshiSeries: "KXHIGHTHOU", nwsLocation: "HOU", iemNetwork: "TX_ASOS" },
  { city: "Las Vegas", station: "KLAS", timezone: "America/Los_Angeles", kalshiSeries: "KXHIGHTLV", nwsLocation: "LAS", iemNetwork: "NV_ASOS" },
  { city: "Minneapolis", station: "KMSP", timezone: "America/Chicago", kalshiSeries: "KXHIGHTMIN", nwsLocation: "MSP", iemNetwork: "MN_ASOS" },
  { city: "New Orleans", station: "KMSY", timezone: "America/Chicago", kalshiSeries: "KXHIGHTNOLA", nwsLocation: "MSY", iemNetwork: "LA_ASOS" },
  { city: "Oklahoma City", station: "KOKC", timezone: "America/Chicago", kalshiSeries: "KXHIGHTOKC", nwsLocation: "OKC", iemNetwork: "OK_ASOS" },
  { city: "Phoenix", station: "KPHX", timezone: "America/Phoenix", kalshiSeries: "KXHIGHTPHX", nwsLocation: "PHX", iemNetwork: "AZ_ASOS" },
  { city: "San Antonio", station: "KSAT", timezone: "America/Chicago", kalshiSeries: "KXHIGHTSATX", nwsLocation: "SAT", iemNetwork: "TX_ASOS" },
  { city: "Seattle", station: "KSEA", timezone: "America/Los_Angeles", kalshiSeries: "KXHIGHTSEA", nwsLocation: "SEA", iemNetwork: "WA_ASOS" },
  { city: "San Francisco", station: "KSFO", timezone: "America/Los_Angeles", kalshiSeries: "KXHIGHTSFO", nwsLocation: "SFO", iemNetwork: "CA_ASOS" }
];

export const SOURCE_USER_AGENT =
  process.env.SOURCE_USER_AGENT ?? "MercuryEdge/0.1 research@example.com";

export const KALSHI_BASE_URL =
  process.env.KALSHI_BASE_URL ?? "https://external-api.kalshi.com/trade-api/v2";

export const AWC_BASE_URL = "https://aviationweather.gov/api/data";
export const NWS_BASE_URL = "https://api.weather.gov";
export const IEM_BASE_URL = "https://mesonet.agron.iastate.edu";
