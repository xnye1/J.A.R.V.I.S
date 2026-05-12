/**
 * JARVIS Mobile Terminal — Phase 11.1 Scaffold
 * Expo app with expo-battery + expo-calendar integration.
 *
 * Install deps:
 *   npx expo install expo-battery expo-calendar
 *
 * Status: scaffold / Phase 11.2 pivoted to laptop-centric.
 *         Activate when iOS device is available.
 */

import React, { useEffect, useState, useRef, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, FlatList,
  StyleSheet, SafeAreaView, Switch, Platform,
} from 'react-native';
import * as Battery  from 'expo-battery';
import * as Calendar from 'expo-calendar';

// ─── Config ────────────────────────────────────────────────────────────────
const ORACLE_HOST = '158.180.78.104';
const ORACLE_PORT = 8000;
const WS_URL      = `ws://${ORACLE_HOST}:${ORACLE_PORT}/ws`;
const API_BASE    = `http://${ORACLE_HOST}:${ORACLE_PORT}`;

const CYAN = '#00f2ff';
const BG   = '#000814';
const DIM  = '#475569';

// ─── Battery Hook ──────────────────────────────────────────────────────────
function useBattery() {
  const [level, setLevel]   = useState(null);
  const [charging, setChg]  = useState(false);

  useEffect(() => {
    let sub;
    Battery.getBatteryLevelAsync().then(v => setLevel(Math.round(v * 100)));
    Battery.getBatteryStateAsync().then(s =>
      setChg(s === Battery.BatteryState.CHARGING || s === Battery.BatteryState.FULL));

    sub = Battery.addBatteryLevelListener(({ batteryLevel }) =>
      setLevel(Math.round(batteryLevel * 100)));
    return () => sub?.remove();
  }, []);

  return { level, charging };
}

// ─── Calendar Hook ─────────────────────────────────────────────────────────
function useTodayEvents() {
  const [events, setEvents] = useState([]);
  const [granted, setGranted] = useState(false);

  useEffect(() => {
    (async () => {
      const { status } = await Calendar.requestCalendarPermissionsAsync();
      if (status !== 'granted') return;
      setGranted(true);

      const calendars = await Calendar.getCalendarsAsync(Calendar.EntityTypes.EVENT);
      const calIds = calendars.map(c => c.id);

      const start = new Date(); start.setHours(0, 0, 0, 0);
      const end   = new Date(); end.setHours(23, 59, 59, 999);

      const raw = await Calendar.getEventsAsync(calIds, start, end);
      setEvents(raw.map(e => ({
        id:       e.id,
        time:     new Date(e.startDate).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }),
        title:    e.title || '(제목 없음)',
        location: e.location || '',
      })));
    })();
  }, []);

  return { events, granted };
}

// ─── Reactor State ─────────────────────────────────────────────────────────
function useReactor(ws) {
  const [energySaving, setEnergy] = useState(false);

  const toggle = useCallback(() => {
    const next = !energySaving;
    setEnergy(next);
    if (ws.current?.readyState === WebSocket.OPEN)
      ws.current.send(JSON.stringify({ type: 'reactor_toggle', energy_saving: next }));
  }, [energySaving, ws]);

  return { energySaving, toggle, setEnergy };
}

// ─── Main App ───────────────────────────────────────────────────────────────
export default function App() {
  const ws      = useRef(null);
  const [connected, setConnected] = useState(false);
  const [log, setLog]             = useState([]);

  const bat                       = useBattery();
  const { events, granted }       = useTodayEvents();
  const { energySaving, toggle, setEnergy } = useReactor(ws);

  const addLog = (text) =>
    setLog(prev => [{ id: Date.now().toString(), text }, ...prev].slice(0, 50));

  // ── WebSocket ──────────────────────────────────────────────────────────
  useEffect(() => {
    function connect() {
      const sock = new WebSocket(WS_URL);
      ws.current = sock;

      sock.onopen = () => {
        sock.send(JSON.stringify({ type: 'register', device: 'remote' }));
        setConnected(true);
        addLog('Neural link active.');
      };
      sock.onclose = () => {
        setConnected(false);
        addLog('Connection lost — retrying in 3s…');
        setTimeout(connect, 3000);
      };
      sock.onmessage = ({ data }) => {
        const m = JSON.parse(data);
        if (m.type === 'reactor_state') setEnergy(m.energy_saving);
        if (m.type === 'chat_response') addLog(`JARVIS: ${m.message}`);
      };
    }
    connect();
    return () => ws.current?.close();
  }, []);

  // ── Battery → server (respects energy saving interval) ────────────────
  useEffect(() => {
    if (bat.level == null || !connected) return;
    const interval = energySaving ? 600_000 : 500;
    const id = setInterval(() => {
      if (ws.current?.readyState === WebSocket.OPEN)
        ws.current.send(JSON.stringify({
          type: 'remote_status', battery: bat.level, plugged: bat.charging,
        }));
    }, interval);
    return () => clearInterval(id);
  }, [bat.level, bat.charging, connected, energySaving]);

  // ── Push today's calendar ──────────────────────────────────────────────
  const pushCalendar = () => {
    if (!connected || events.length === 0) return;
    const today = new Date().toLocaleDateString('ko-KR');
    ws.current.send(JSON.stringify({ type: 'calendar_data', date: today, events }));
    addLog(`Schedule synced: ${events.length} event(s) → HUD`);
  };

  return (
    <SafeAreaView style={s.safe}>
      {/* Header */}
      <View style={s.header}>
        <View style={[s.dot, { backgroundColor: connected ? '#22c55e' : '#ef4444' }]} />
        <Text style={s.brand}>J.A.R.V.I.S  MOBILE</Text>
        <Text style={s.bat}>{bat.level != null ? `🔋 ${bat.level}%` : '--'}</Text>
      </View>

      {/* Reactor toggle */}
      <View style={s.reactorRow}>
        <Text style={s.reactorLbl}>REACTOR POWER</Text>
        <Text style={[s.reactorVal, energySaving && s.saving]}>
          {energySaving ? 'ENERGY SAVING' : 'FULL POWER'}
        </Text>
        <Switch
          value={!energySaving}
          onValueChange={toggle}
          trackColor={{ false: '#374151', true: 'rgba(0,242,255,0.35)' }}
          thumbColor={energySaving ? DIM : CYAN}
        />
      </View>

      {/* Calendar */}
      <View style={s.calBox}>
        <Text style={s.calTitle}>TODAY  —  {granted ? `${events.length} EVENTS` : 'NO PERMISSION'}</Text>
        <FlatList
          data={events}
          keyExtractor={e => e.id}
          renderItem={({ item }) => (
            <View style={s.calRow}>
              <Text style={s.calTime}>{item.time}</Text>
              <Text style={s.calEvt}>{item.title}</Text>
            </View>
          )}
          ListEmptyComponent={<Text style={s.calEmpty}>캘린더 권한이 필요합니다.</Text>}
          style={{ maxHeight: 160 }}
        />
        <TouchableOpacity style={s.calBtn} onPress={pushCalendar} activeOpacity={0.75}>
          <Text style={s.calBtnTxt}>📅  SYNC SCHEDULE → HUD</Text>
        </TouchableOpacity>
      </View>

      {/* Event log */}
      <FlatList
        data={log}
        keyExtractor={i => i.id}
        renderItem={({ item }) => <Text style={s.logLine}>{item.text}</Text>}
        style={s.logBox}
      />
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  safe:       { flex: 1, backgroundColor: BG, padding: 16 },
  header:     { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  dot:        { width: 8, height: 8, borderRadius: 4 },
  brand:      { color: CYAN, fontSize: 13, fontWeight: '700', letterSpacing: 4, flex: 1 },
  bat:        { color: CYAN, fontSize: 12 },

  reactorRow: { flexDirection: 'row', alignItems: 'center', gap: 8,
                backgroundColor: 'rgba(10,22,40,0.9)', borderRadius: 12,
                borderWidth: 1, borderColor: 'rgba(0,242,255,0.22)', padding: 12, marginBottom: 10 },
  reactorLbl: { color: DIM, fontSize: 9, letterSpacing: 2, flex: 1 },
  reactorVal: { color: CYAN, fontSize: 9, letterSpacing: 2, fontWeight: '700' },
  saving:     { color: '#fbbf24' },

  calBox:     { backgroundColor: 'rgba(10,22,40,0.9)', borderRadius: 12,
                borderWidth: 1, borderColor: 'rgba(0,242,255,0.22)', padding: 12, marginBottom: 10 },
  calTitle:   { color: CYAN, fontSize: 8, letterSpacing: 3, marginBottom: 8 },
  calRow:     { flexDirection: 'row', gap: 10, paddingVertical: 4,
                borderBottomWidth: 1, borderBottomColor: 'rgba(0,242,255,0.07)' },
  calTime:    { color: CYAN, fontSize: 10, width: 40 },
  calEvt:     { color: '#cbd5e1', fontSize: 10, flex: 1 },
  calEmpty:   { color: DIM, fontSize: 10 },
  calBtn:     { marginTop: 8, padding: 9, borderRadius: 10,
                backgroundColor: 'rgba(0,242,255,0.06)',
                borderWidth: 1, borderColor: 'rgba(0,242,255,0.3)', alignItems: 'center' },
  calBtnTxt:  { color: CYAN, fontSize: 10, letterSpacing: 2 },

  logBox:     { flex: 1, backgroundColor: 'rgba(4,14,28,0.6)', borderRadius: 12,
                borderWidth: 1, borderColor: 'rgba(0,242,255,0.12)', padding: 10 },
  logLine:    { color: '#94a3b8', fontSize: 10, lineHeight: 18 },
});
