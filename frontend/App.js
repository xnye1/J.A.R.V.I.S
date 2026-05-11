import 'react-native-gesture-handler';
import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  FlatList,
  KeyboardAvoidingView,
  Platform,
  SafeAreaView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withTiming,
  withRepeat,
  withSequence,
  Easing,
} from 'react-native-reanimated';
import { LinearGradient } from 'expo-linear-gradient';
import JarvisOrb from './components/JarvisOrb';
import StatusHub from './components/StatusHub';

// ── Endpoints (driven by config.js — swap ORACLE_IP there for production) ─────
import config from './config';
const WS_URL  = config.WS_URL;
const API_URL = config.API_URL;
const CYAN     = '#00f2ff';
const BG       = '#000814';
const TEXT     = '#cbd5e1';
const TEXT_DIM = '#475569';
const GLASS    = 'rgba(30, 41, 59, 0.40)';

// ── Scan line overlay across full screen ─────────────────────────────────────
function GlobalScanLine() {
  const y = useSharedValue(-10);
  useEffect(() => {
    y.value = withRepeat(
      withTiming(900, { duration: 5000, easing: Easing.linear }),
      -1, false,
    );
  }, []);
  const style = useAnimatedStyle(() => ({
    transform: [{ translateY: y.value }],
  }));
  return <Animated.View style={[scan.line, style]} pointerEvents="none" />;
}

// ── Typing indicator ──────────────────────────────────────────────────────────
function TypingDots() {
  const op1 = useSharedValue(0.2);
  const op2 = useSharedValue(0.2);
  const op3 = useSharedValue(0.2);
  useEffect(() => {
    const t = { duration: 400 };
    op1.value = withRepeat(withSequence(withTiming(1, t), withTiming(0.2, t), withTiming(0.2, { duration: 800 })), -1);
    setTimeout(() => { op2.value = withRepeat(withSequence(withTiming(0.2, t), withTiming(1, t), withTiming(0.2, { duration: 800 })), -1); }, 267);
    setTimeout(() => { op3.value = withRepeat(withSequence(withTiming(0.2, t), withTiming(0.2, t), withTiming(1, t), withTiming(0.2, t)), -1); }, 534);
  }, []);
  const s1 = useAnimatedStyle(() => ({ opacity: op1.value }));
  const s2 = useAnimatedStyle(() => ({ opacity: op2.value }));
  const s3 = useAnimatedStyle(() => ({ opacity: op3.value }));
  return (
    <View style={typing.row}>
      <Text style={typing.label}>JARVIS</Text>
      <View style={typing.dots}>
        <Animated.View style={[typing.dot, s1]} />
        <Animated.View style={[typing.dot, s2]} />
        <Animated.View style={[typing.dot, s3]} />
      </View>
    </View>
  );
}

// ── Chat bubble ───────────────────────────────────────────────────────────────
function Bubble({ item }) {
  const isUser  = item.role === 'user';
  const isAlert = item.role === 'alert';
  return (
    <View style={[bub.wrap, isUser ? bub.wrapUser : bub.wrapJarvis]}>
      {!isUser && (
        <Text style={[bub.sender, isAlert && bub.senderAlert]}>
          {isAlert ? 'JARVIS ALERT' : 'JARVIS'}
        </Text>
      )}
      <View style={[bub.bubble, isUser ? bub.user : isAlert ? bub.alert : bub.jarvis]}>
        <Text style={[bub.text, isAlert && bub.textAlert]}>{item.text}</Text>
      </View>
    </View>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [messages, setMessages]   = useState([
    { id: '0', role: 'jarvis', text: 'Good day, Sir. All systems are online. How may I be of assistance?' },
  ]);
  const [input, setInput]         = useState('');
  const [speaking, setSpeaking]   = useState(false);
  const [typing, setTyping]       = useState(false);
  const [connected, setConnected] = useState(false);
  const [status, setStatus]       = useState({});

  const ws      = useRef(null);
  const listRef = useRef(null);

  const addMessage = useCallback((role, text) => {
    setMessages(prev => [...prev, { id: Date.now().toString(), role, text }]);
    setTimeout(() => listRef.current?.scrollToEnd({ animated: true }), 80);
  }, []);

  // WebSocket connection
  useEffect(() => {
    const connect = () => {
      const socket = new WebSocket(WS_URL);
      ws.current = socket;

      socket.onopen  = () => setConnected(true);
      socket.onclose = () => { setConnected(false); setTimeout(connect, 3000); };

      socket.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'chat') {
          setTyping(false);
          setSpeaking(true);
          addMessage('jarvis', data.message);
          setTimeout(() => setSpeaking(false), 2500);
        } else if (data.type === 'proactive_alert') {
          setSpeaking(true);
          addMessage('alert', data.message);
          setTimeout(() => setSpeaking(false), 3000);
        }
      };
    };
    connect();
    return () => ws.current?.close();
  }, [addMessage]);

  // Poll system status every 15s
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`${API_URL}/status`);
        if (r.ok) setStatus(await r.json());
      } catch (_) {}
    };
    poll();
    const id = setInterval(poll, 15000);
    return () => clearInterval(id);
  }, []);

  const send = useCallback(() => {
    const text = input.trim();
    if (!text || !ws.current || ws.current.readyState !== WebSocket.OPEN) return;
    addMessage('user', text);
    setTyping(true);
    ws.current.send(JSON.stringify({ type: 'chat', message: text }));
    setInput('');
  }, [input, addMessage]);

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar barStyle="light-content" backgroundColor={BG} translucent />

      {/* Deep space background */}
      <LinearGradient
        colors={['#000814', '#00111f', '#000814']}
        start={{ x: 0.5, y: 0 }}
        end={{ x: 0.5, y: 1 }}
        style={StyleSheet.absoluteFill}
      />

      {/* Full-screen scan line */}
      <GlobalScanLine />

      <KeyboardAvoidingView
        style={s.root}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : 0}
      >
        {/* ── Top HUD bar ── */}
        <View style={s.topBar}>
          <View style={s.topLeft}>
            <View style={[s.topDot, { backgroundColor: connected ? '#22c55e' : '#ef4444' }]} />
            <Text style={s.topLabel}>{connected ? 'NEURAL LINK ACTIVE' : 'RECONNECTING...'}</Text>
          </View>
          <Text style={s.topTitle}>J.A.R.V.I.S</Text>
          <Text style={s.topVer}>v1.0</Text>
        </View>

        {/* ── Central Orb ── */}
        <View style={s.orbSection}>
          <JarvisOrb speaking={speaking} />
          <Text style={s.orbSubLabel}>
            {speaking ? 'PROCESSING...' : typing ? 'THINKING...' : 'STANDBY'}
          </Text>
        </View>

        {/* ── Chat window ── */}
        <View style={s.chatPanel}>
          <FlatList
            ref={listRef}
            data={messages}
            keyExtractor={i => i.id}
            renderItem={({ item }) => <Bubble item={item} />}
            style={s.list}
            contentContainerStyle={{ paddingVertical: 8 }}
            showsVerticalScrollIndicator={false}
          />
          {typing && (
            <View style={s.typingWrap}>
              <TypingDots />
            </View>
          )}
        </View>

        {/* ── Input row ── */}
        <View style={s.inputRow}>
          <TextInput
            style={s.input}
            value={input}
            onChangeText={setInput}
            placeholder="Issue a command, Sir…"
            placeholderTextColor={TEXT_DIM}
            onSubmitEditing={send}
            returnKeyType="send"
            selectionColor={CYAN}
          />
          <TouchableOpacity
            style={[s.sendBtn, !input.trim() && s.sendBtnDim]}
            onPress={send}
            activeOpacity={0.7}
          >
            <Text style={s.sendIcon}>▶</Text>
          </TouchableOpacity>
        </View>

        {/* ── Status HUD ── */}
        <View style={s.statusSection}>
          <StatusHub status={status} connected={connected} />
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────
const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: BG },
  root: { flex: 1, paddingHorizontal: 16 },

  // Top HUD bar
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: 14,
    paddingBottom: 8,
  },
  topLeft: { flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1 },
  topDot: { width: 6, height: 6, borderRadius: 3 },
  topLabel: { color: TEXT_DIM, fontSize: 9, letterSpacing: 2 },
  topTitle: { color: CYAN, fontSize: 13, fontWeight: '700', letterSpacing: 4 },
  topVer: { color: TEXT_DIM, fontSize: 9, letterSpacing: 1, flex: 1, textAlign: 'right' },

  // Orb section
  orbSection: { alignItems: 'center', paddingVertical: 8 },
  orbSubLabel: {
    color: TEXT_DIM,
    fontSize: 9,
    letterSpacing: 4,
    marginTop: 8,
  },

  // Chat panel — glassmorphism
  chatPanel: {
    flex: 1,
    backgroundColor: GLASS,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(0, 242, 255, 0.15)',
    overflow: 'hidden',
    marginVertical: 8,
  },
  list: { flex: 1, paddingHorizontal: 12 },
  typingWrap: { paddingHorizontal: 12, paddingBottom: 6 },

  // Input
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
  },
  input: {
    flex: 1,
    height: 44,
    backgroundColor: 'rgba(15, 23, 42, 0.85)',
    color: TEXT,
    borderRadius: 22,
    paddingHorizontal: 18,
    fontSize: 13,
    borderWidth: 1,
    borderColor: 'rgba(0, 242, 255, 0.25)',
  },
  sendBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: CYAN,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: CYAN,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.9,
    shadowRadius: 12,
    elevation: 12,
  },
  sendBtnDim: { opacity: 0.4 },
  sendIcon: { color: BG, fontSize: 14, fontWeight: '800', marginLeft: 2 },

  // Status section
  statusSection: { marginBottom: 12 },
});

// Scan line
const scan = StyleSheet.create({
  line: {
    position: 'absolute',
    left: 0,
    right: 0,
    height: 1,
    backgroundColor: 'rgba(0, 242, 255, 0.04)',
    zIndex: 1,
  },
});

// Bubble styles
const bub = StyleSheet.create({
  wrap: { marginVertical: 4 },
  wrapUser:   { alignItems: 'flex-end' },
  wrapJarvis: { alignItems: 'flex-start' },
  sender: { color: CYAN, fontSize: 8, letterSpacing: 2, marginBottom: 3, marginLeft: 12 },
  senderAlert: { color: '#ef4444' },
  bubble: { maxWidth: '82%', borderRadius: 14, paddingHorizontal: 14, paddingVertical: 9 },
  user:   { backgroundColor: 'rgba(0, 242, 255, 0.12)', borderBottomRightRadius: 4, borderWidth: 1, borderColor: 'rgba(0,242,255,0.2)' },
  jarvis: { backgroundColor: 'rgba(15, 23, 42, 0.7)', borderBottomLeftRadius: 4, borderLeftWidth: 2, borderLeftColor: CYAN },
  alert:  { backgroundColor: 'rgba(239,68,68,0.08)', borderLeftWidth: 2, borderLeftColor: '#ef4444', borderRadius: 14 },
  text:      { color: TEXT, fontSize: 13, lineHeight: 19 },
  textAlert: { color: '#fca5a5' },
});

// Typing indicator
const typing = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  label: { color: CYAN, fontSize: 8, letterSpacing: 2 },
  dots: { flexDirection: 'row', gap: 4 },
  dot: { width: 5, height: 5, borderRadius: 2.5, backgroundColor: CYAN },
});
