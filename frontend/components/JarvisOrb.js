import React, { useEffect } from 'react';
import { View, StyleSheet } from 'react-native';
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withRepeat,
  withTiming,
  withSequence,
  Easing,
  interpolate,
} from 'react-native-reanimated';

const CYAN = '#00f2ff';
const CYAN_DIM = 'rgba(0, 242, 255, 0.15)';
const CYAN_MID = 'rgba(0, 242, 255, 0.35)';

// voiceAmplitude: 0.0–1.0, injected later from audio analysis
export default function JarvisOrb({ voiceAmplitude = 0, speaking = false }) {
  const pulse      = useSharedValue(1);
  const glow       = useSharedValue(0.5);
  const rotateOut  = useSharedValue(0);
  const rotateIn   = useSharedValue(0);
  const scanLine   = useSharedValue(-120);
  const ripple1    = useSharedValue(1);
  const ripple2    = useSharedValue(1);

  useEffect(() => {
    // Core breath
    pulse.value = withRepeat(
      withSequence(
        withTiming(1.10, { duration: 1800, easing: Easing.inOut(Easing.sin) }),
        withTiming(0.93, { duration: 1800, easing: Easing.inOut(Easing.sin) }),
      ),
      -1, false,
    );

    // Ambient glow intensity
    glow.value = withRepeat(
      withSequence(
        withTiming(1,   { duration: 2000, easing: Easing.inOut(Easing.sin) }),
        withTiming(0.3, { duration: 2000, easing: Easing.inOut(Easing.sin) }),
      ),
      -1, false,
    );

    // Outer ring — clockwise
    rotateOut.value = withRepeat(
      withTiming(360, { duration: 10000, easing: Easing.linear }),
      -1, false,
    );

    // Inner ring — counter-clockwise, faster
    rotateIn.value = withRepeat(
      withTiming(-360, { duration: 6000, easing: Easing.linear }),
      -1, false,
    );

    // Scan line crossing the orb top→bottom
    scanLine.value = withRepeat(
      withTiming(120, { duration: 2400, easing: Easing.inOut(Easing.quad) }),
      -1, true,
    );

    // Ripple 1
    ripple1.value = withRepeat(
      withTiming(2.2, { duration: 3000, easing: Easing.out(Easing.exp) }),
      -1, false,
    );

    // Ripple 2 — offset start
    setTimeout(() => {
      ripple2.value = withRepeat(
        withTiming(2.2, { duration: 3000, easing: Easing.out(Easing.exp) }),
        -1, false,
      );
    }, 1500);
  }, []);

  // Voice-reactive scale on top of breath pulse
  const coreStyle = useAnimatedStyle(() => {
    const voiceBoost = voiceAmplitude * 0.4;
    return {
      transform: [{ scale: pulse.value + voiceBoost }],
      shadowOpacity: interpolate(glow.value, [0, 1], [0.5, 1.0]),
      shadowRadius: interpolate(glow.value, [0, 1], [20, 44]) + voiceAmplitude * 20,
    };
  });

  const outerRingStyle = useAnimatedStyle(() => ({
    transform: [{ rotate: `${rotateOut.value}deg` }],
    opacity: interpolate(glow.value, [0, 1], [0.25, 0.6]),
  }));

  const innerRingStyle = useAnimatedStyle(() => ({
    transform: [{ rotate: `${rotateIn.value}deg` }],
    opacity: interpolate(glow.value, [0, 1], [0.4, 0.85]),
  }));

  const scanStyle = useAnimatedStyle(() => ({
    transform: [{ translateY: scanLine.value }],
    opacity: interpolate(
      Math.abs(scanLine.value),
      [0, 80, 120],
      [0.8, 0.2, 0],
    ),
  }));

  const ripple1Style = useAnimatedStyle(() => ({
    transform: [{ scale: ripple1.value }],
    opacity: interpolate(ripple1.value, [1, 2.2], [0.5, 0]),
  }));

  const ripple2Style = useAnimatedStyle(() => ({
    transform: [{ scale: ripple2.value }],
    opacity: interpolate(ripple2.value, [1, 2.2], [0.35, 0]),
  }));

  return (
    <View style={styles.container}>
      {/* Ripple waves radiating outward */}
      <Animated.View style={[styles.ripple, ripple1Style]} />
      <Animated.View style={[styles.ripple, ripple2Style]} />

      {/* Outer dashed orbit ring */}
      <Animated.View style={[styles.outerRing, outerRingStyle]}>
        {Array.from({ length: 24 }).map((_, i) => (
          <View
            key={i}
            style={[
              styles.orbitDot,
              {
                transform: [
                  { rotate: `${i * 15}deg` },
                  { translateX: 100 },
                ],
                opacity: i % 3 === 0 ? 0.9 : 0.35,
              },
            ]}
          />
        ))}
      </Animated.View>

      {/* Inner rotating arc ring */}
      <Animated.View style={[styles.innerRing, innerRingStyle]} />

      {/* Core orb with glow */}
      <Animated.View style={[styles.core, coreStyle]}>
        {/* Scan line clipped inside core */}
        <View style={styles.scanClip}>
          <Animated.View style={[styles.scanLine, scanStyle]} />
        </View>
        {/* Bright center */}
        <View style={styles.coreCenter} />
      </Animated.View>

      {/* Corner HUD brackets */}
      <View style={[styles.bracket, styles.bracketTL]} />
      <View style={[styles.bracket, styles.bracketTR]} />
      <View style={[styles.bracket, styles.bracketBL]} />
      <View style={[styles.bracket, styles.bracketBR]} />
    </View>
  );
}

const ORB_SIZE     = 130;
const INNER_RING   = 168;
const OUTER_RING   = 214;
const RIPPLE_BASE  = 130;
const BRACKET      = 24;

const styles = StyleSheet.create({
  container: {
    width: OUTER_RING + 40,
    height: OUTER_RING + 40,
    alignItems: 'center',
    justifyContent: 'center',
  },

  // Ripples
  ripple: {
    position: 'absolute',
    width: RIPPLE_BASE,
    height: RIPPLE_BASE,
    borderRadius: RIPPLE_BASE / 2,
    borderWidth: 1.5,
    borderColor: CYAN,
  },

  // Outer dot orbit
  outerRing: {
    position: 'absolute',
    width: OUTER_RING,
    height: OUTER_RING,
    alignItems: 'center',
    justifyContent: 'center',
  },
  orbitDot: {
    position: 'absolute',
    width: 3,
    height: 3,
    borderRadius: 1.5,
    backgroundColor: CYAN,
  },

  // Inner arc ring
  innerRing: {
    position: 'absolute',
    width: INNER_RING,
    height: INNER_RING,
    borderRadius: INNER_RING / 2,
    borderWidth: 1.5,
    borderColor: CYAN,
    borderStyle: 'solid',
    borderTopColor: 'transparent',
    borderLeftColor: 'transparent',
  },

  // Core orb
  core: {
    width: ORB_SIZE,
    height: ORB_SIZE,
    borderRadius: ORB_SIZE / 2,
    backgroundColor: 'rgba(0, 22, 36, 0.95)',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1.5,
    borderColor: CYAN,
    shadowColor: CYAN,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.9,
    shadowRadius: 30,
    elevation: 30,
    overflow: 'hidden',
  },
  scanClip: {
    position: 'absolute',
    width: '100%',
    height: '100%',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  scanLine: {
    width: '120%',
    height: 1.5,
    backgroundColor: CYAN,
    shadowColor: CYAN,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 8,
  },
  coreCenter: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: CYAN,
    shadowColor: CYAN,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 20,
    elevation: 20,
  },

  // HUD corner brackets
  bracket: {
    position: 'absolute',
    width: BRACKET,
    height: BRACKET,
    borderColor: CYAN,
    opacity: 0.7,
  },
  bracketTL: { top: 0,  left: 0,  borderTopWidth: 2, borderLeftWidth: 2 },
  bracketTR: { top: 0,  right: 0, borderTopWidth: 2, borderRightWidth: 2 },
  bracketBL: { bottom: 0, left: 0,  borderBottomWidth: 2, borderLeftWidth: 2 },
  bracketBR: { bottom: 0, right: 0, borderBottomWidth: 2, borderRightWidth: 2 },
});
