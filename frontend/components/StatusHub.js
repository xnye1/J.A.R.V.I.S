import React, { useEffect } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withRepeat,
  withTiming,
  withSequence,
  Easing,
} from 'react-native-reanimated';

const CYAN        = '#00f2ff';
const CYAN_FAINT  = 'rgba(0, 242, 255, 0.12)';
const CYAN_BORDER = 'rgba(0, 242, 255, 0.28)';
const GLASS       = 'rgba(30, 41, 59, 0.40)';
const TEXT        = '#cbd5e1';
const TEXT_DIM    = '#64748b';

// ── Micro battery icon (pure View) ───────────────────────────────────────────
function BatteryIcon({ percent, charging }) {
  const fillColor =
    charging   ? '#22c55e' :
    percent < 20 ? '#ef4444' :
    percent < 40 ? '#f59e0b' : CYAN;

  return (
    <View style={bat.wrap}>
      <View style={bat.body}>
        <View style={[bat.fill, { width: `${Math.max(4, percent)}%`, backgroundColor: fillColor }]} />
        {charging && <Text style={bat.bolt}>⚡</Text>}
      </View>
      <View style={bat.nub} />
    </View>
  );
}

// ── Single stat row ───────────────────────────────────────────────────────────
function StatRow({ label, value, unit = '', warn = false }) {
  const barWidth = typeof value === 'number' ? `${Math.min(100, value)}%` : '0%';
  const barColor = warn ? '#ef4444' : CYAN;

  return (
    <View style={stat.row}>
      <Text style={stat.label}>{label}</Text>
      <View style={stat.barTrack}>
        <View style={[stat.barFill, { width: barWidth, backgroundColor: barColor }]} />
      </View>
      <Text style={[stat.value, warn && stat.warn]}>{value}{unit}</Text>
    </View>
  );
}

// ── Blinking status dot ───────────────────────────────────────────────────────
function StatusDot({ online }) {
  const opacity = useSharedValue(1);
  useEffect(() => {
    if (online) {
      opacity.value = withRepeat(
        withSequence(
          withTiming(0.2, { duration: 800 }),
          withTiming(1.0, { duration: 800 }),
        ),
        -1, false,
      );
    } else {
      opacity.value = 0.4;
    }
  }, [online]);
  const animStyle = useAnimatedStyle(() => ({ opacity: opacity.value }));
  return (
    <Animated.View
      style={[
        s.dot,
        { backgroundColor: online ? '#22c55e' : '#ef4444' },
        animStyle,
      ]}
    />
  );
}

// ── Main export ───────────────────────────────────────────────────────────────
export default function StatusHub({ status = {}, connected = false }) {
  const {
    battery_percent = null,
    battery_plugged  = false,
    cpu_percent      = 0,
    memory_percent   = 0,
    alerts           = [],
  } = status;

  const glowAnim = useSharedValue(0.28);
  useEffect(() => {
    glowAnim.value = withRepeat(
      withSequence(
        withTiming(0.7, { duration: 2500, easing: Easing.inOut(Easing.sin) }),
        withTiming(0.2, { duration: 2500, easing: Easing.inOut(Easing.sin) }),
      ),
      -1, false,
    );
  }, []);

  const panelGlow = useAnimatedStyle(() => ({
    shadowOpacity: glowAnim.value,
  }));

  return (
    <Animated.View style={[s.panel, panelGlow]}>
      {/* Panel header */}
      <View style={s.header}>
        <View style={s.headerLeft}>
          <StatusDot online={connected} />
          <Text style={s.headerText}>{connected ? 'SYS ONLINE' : 'CONNECTING'}</Text>
        </View>
        <Text style={s.headerLabel}>SYSTEM STATUS</Text>
      </View>

      <View style={s.divider} />

      {/* Battery row */}
      {battery_percent !== null && (
        <View style={s.batteryRow}>
          <BatteryIcon percent={battery_percent} charging={battery_plugged} />
          <Text style={s.batteryText}>
            {battery_percent.toFixed(0)}%
          </Text>
          <Text style={s.batteryHint}>
            {battery_plugged ? 'CHARGING' : 'ON BATTERY'}
          </Text>
          {!battery_plugged && battery_percent < 20 && (
            <View style={s.alertBadge}>
              <Text style={s.alertBadgeText}>LOW</Text>
            </View>
          )}
        </View>
      )}

      {/* CPU & Memory bars */}
      <View style={s.statsBlock}>
        <StatRow
          label="CPU"
          value={cpu_percent.toFixed(0)}
          unit="%"
          warn={cpu_percent >= 85}
        />
        <StatRow
          label="MEM"
          value={memory_percent.toFixed(0)}
          unit="%"
          warn={memory_percent >= 90}
        />
      </View>

      {/* Live alerts from Proactive Engine */}
      {alerts.length > 0 && (
        <View style={s.alertsBlock}>
          {alerts.map((a, i) => (
            <View key={i} style={s.alertRow}>
              <Text style={s.alertIcon}>!</Text>
              <Text style={s.alertText} numberOfLines={2}>{a}</Text>
            </View>
          ))}
        </View>
      )}
    </Animated.View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────
const s = StyleSheet.create({
  panel: {
    width: '100%',
    backgroundColor: GLASS,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: CYAN_BORDER,
    padding: 16,
    shadowColor: CYAN,
    shadowOffset: { width: 0, height: 0 },
    shadowRadius: 18,
    elevation: 12,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  dot: { width: 7, height: 7, borderRadius: 3.5 },
  headerText: { color: CYAN, fontSize: 10, fontWeight: '700', letterSpacing: 2 },
  headerLabel: { color: TEXT_DIM, fontSize: 9, letterSpacing: 3 },
  divider: { height: 1, backgroundColor: CYAN_BORDER, marginBottom: 12 },

  // Battery
  batteryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 14,
    backgroundColor: CYAN_FAINT,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  batteryText: { color: CYAN, fontSize: 18, fontWeight: '700', minWidth: 48 },
  batteryHint: { color: TEXT_DIM, fontSize: 9, letterSpacing: 2, flex: 1 },
  alertBadge: { backgroundColor: '#ef4444', borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  alertBadgeText: { color: '#fff', fontSize: 8, fontWeight: '800', letterSpacing: 1 },

  // Stats
  statsBlock: { gap: 8 },

  // Alerts
  alertsBlock: { marginTop: 12, gap: 6 },
  alertRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    backgroundColor: 'rgba(239,68,68,0.08)',
    borderRadius: 8,
    borderLeftWidth: 2,
    borderLeftColor: '#ef4444',
    padding: 8,
  },
  alertIcon: { color: '#ef4444', fontSize: 11, fontWeight: '800', marginTop: 1 },
  alertText: { color: '#fca5a5', fontSize: 11, lineHeight: 16, flex: 1 },
});

// Battery icon styles
const bat = StyleSheet.create({
  wrap: { flexDirection: 'row', alignItems: 'center' },
  body: {
    width: 28,
    height: 14,
    borderRadius: 3,
    borderWidth: 1.5,
    borderColor: TEXT_DIM,
    overflow: 'hidden',
    justifyContent: 'center',
    alignItems: 'center',
  },
  fill: { position: 'absolute', left: 0, top: 0, bottom: 0, borderRadius: 2 },
  bolt: { position: 'absolute', fontSize: 8, zIndex: 2 },
  nub: {
    width: 3,
    height: 7,
    backgroundColor: TEXT_DIM,
    borderTopRightRadius: 2,
    borderBottomRightRadius: 2,
  },
});

// Stat row styles
const stat = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  label: { color: TEXT_DIM, fontSize: 9, letterSpacing: 2, width: 28 },
  barTrack: {
    flex: 1,
    height: 4,
    backgroundColor: 'rgba(100,116,139,0.25)',
    borderRadius: 2,
    overflow: 'hidden',
  },
  barFill: { height: '100%', borderRadius: 2 },
  value: { color: TEXT, fontSize: 11, fontWeight: '600', width: 36, textAlign: 'right' },
  warn: { color: '#ef4444' },
});
