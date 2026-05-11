// ── JARVIS Frontend Configuration ─────────────────────────────────────────────
// Set ORACLE_IP to your Oracle Cloud public IP before building for production.
// In Expo: __DEV__ is true for `expo start`, false for `expo build`.

const ORACLE_IP = '158.180.78.104';
const PORT      = 8000;

const dev = {
  API_URL : `http://localhost:${PORT}`,
  WS_URL  : `ws://localhost:${PORT}/ws`,
  ENV     : 'development',
};

const prod = {
  API_URL : `http://${ORACLE_IP}:${PORT}`,
  WS_URL  : `ws://${ORACLE_IP}:${PORT}/ws`,
  ENV     : 'production',
};

const config = __DEV__ ? dev : prod;

export default config;

/*
  Usage in any component:

    import config from '../config';
    fetch(config.API_URL + '/chat', { ... });
    new WebSocket(config.WS_URL);
*/
