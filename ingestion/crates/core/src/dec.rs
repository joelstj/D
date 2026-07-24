//! Decimal-string wrappers for 256-bit integers.
//!
//! The engine contract (`docs/reference/INTEGRATION.md §3`) is explicit: reserves
//! reach 2¹¹², `sqrt_price_x96` ≈ 2¹⁶⁰, and `liquidity` reaches 2¹²⁸ — all beyond
//! JSON's safe-integer range (2⁵³). They therefore cross the boundary as **base-10
//! decimal strings**, never JSON numbers, so no precision is silently lost.
//!
//! `alloy_primitives::U256`/`I256` serialize as `0x`-hex by default, which is *not*
//! the contract shape. [`DecU256`] / [`DecI256`] fix that: they are transparent
//! newtypes that (de)serialize as decimal strings and are otherwise ergonomic to
//! use in place of the underlying integer.

use alloy_primitives::{I256, U256};
use core::fmt;
use serde::{de, Deserialize, Deserializer, Serialize, Serializer};

/// A [`U256`] that (de)serializes as a base-10 decimal string.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct DecU256(pub U256);

impl DecU256 {
    /// The additive identity, `0`.
    pub const ZERO: Self = Self(U256::ZERO);

    /// The wrapped [`U256`].
    #[inline]
    pub const fn get(self) -> U256 {
        self.0
    }
}

impl From<U256> for DecU256 {
    #[inline]
    fn from(v: U256) -> Self {
        Self(v)
    }
}

impl From<DecU256> for U256 {
    #[inline]
    fn from(v: DecU256) -> Self {
        v.0
    }
}

impl From<u128> for DecU256 {
    #[inline]
    fn from(v: u128) -> Self {
        Self(U256::from(v))
    }
}

impl From<u64> for DecU256 {
    #[inline]
    fn from(v: u64) -> Self {
        Self(U256::from(v))
    }
}

impl fmt::Display for DecU256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // U256's Display is base-10.
        fmt::Display::fmt(&self.0, f)
    }
}

impl Serialize for DecU256 {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.collect_str(&self.0)
    }
}

impl<'de> Deserialize<'de> for DecU256 {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl de::Visitor<'_> for V {
            type Value = DecU256;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a base-10 decimal string encoding a U256")
            }
            fn visit_str<E: de::Error>(self, s: &str) -> Result<DecU256, E> {
                // Strict base-10: reject hex/whitespace/sign so a producer that
                // "helpfully" sends 0x… or a JSON number is caught, not silently
                // misread. Also reject the empty string, which `from_str_radix`
                // otherwise parses to 0 — a malformed value should error, not zero.
                if s.is_empty() {
                    return Err(E::custom("empty string is not a valid U256"));
                }
                U256::from_str_radix(s, 10)
                    .map(DecU256)
                    .map_err(|e| E::custom(format!("invalid U256 decimal string {s:?}: {e}")))
            }
        }
        d.deserialize_str(V)
    }
}

/// An [`I256`] that (de)serializes as a base-10 decimal string (sign-prefixed for
/// negatives). Provided for completeness of the contract's signed-amount surface.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct DecI256(pub I256);

impl DecI256 {
    /// The wrapped [`I256`].
    #[inline]
    pub const fn get(self) -> I256 {
        self.0
    }
}

impl From<I256> for DecI256 {
    #[inline]
    fn from(v: I256) -> Self {
        Self(v)
    }
}

impl From<DecI256> for I256 {
    #[inline]
    fn from(v: DecI256) -> Self {
        v.0
    }
}

impl fmt::Display for DecI256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(&self.0, f)
    }
}

impl Serialize for DecI256 {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.collect_str(&self.0)
    }
}

impl<'de> Deserialize<'de> for DecI256 {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl de::Visitor<'_> for V {
            type Value = DecI256;
            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a base-10 decimal string encoding an I256")
            }
            fn visit_str<E: de::Error>(self, s: &str) -> Result<DecI256, E> {
                if s.is_empty() {
                    return Err(E::custom("empty string is not a valid I256"));
                }
                s.parse::<I256>()
                    .map(DecI256)
                    .map_err(|e| E::custom(format!("invalid I256 decimal string {s:?}: {e}")))
            }
        }
        d.deserialize_str(V)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn u256_decimal_string_roundtrip_at_scale() {
        // 2^160 (sqrt_price_x96 scale) and 2^112 (reserve scale) must survive
        // string → U256 → string unchanged.
        for shift in [160u32, 112, 128, 255] {
            let v = U256::from(1u8) << shift;
            let d = DecU256(v);
            let json = serde_json::to_string(&d).unwrap();
            assert!(
                json.starts_with('"') && json.ends_with('"'),
                "must be a JSON string: {json}"
            );
            let back: DecU256 = serde_json::from_str(&json).unwrap();
            assert_eq!(d, back, "roundtrip changed the value at 2^{shift}");
            assert_eq!(json, format!("\"{v}\""));
        }
    }

    #[test]
    fn u256_max_roundtrips() {
        let d = DecU256(U256::MAX);
        let json = serde_json::to_string(&d).unwrap();
        let back: DecU256 = serde_json::from_str(&json).unwrap();
        assert_eq!(d, back);
    }

    #[test]
    fn u256_rejects_hex_and_numbers() {
        // The precision-critical rejections: a 0x-hex string (would be misread) and
        // a bare JSON number (the whole reason we use strings). ruint tolerates
        // underscore separators in decimals, which is benign and left as-is.
        assert!(serde_json::from_str::<DecU256>("\"0x10\"").is_err());
        assert!(serde_json::from_str::<DecU256>("16").is_err()); // bare JSON number
        assert!(serde_json::from_str::<DecU256>("\"\"").is_err()); // empty
        assert!(serde_json::from_str::<DecU256>("\"12.3\"").is_err()); // fractional
    }

    #[test]
    fn i256_decimal_string_roundtrip_signed() {
        let big = I256::try_from(i128::MIN).unwrap() * I256::try_from(1_000_000i64).unwrap();
        for v in [I256::ZERO, I256::MINUS_ONE, big, -big] {
            let d = DecI256(v);
            let json = serde_json::to_string(&d).unwrap();
            let back: DecI256 = serde_json::from_str(&json).unwrap();
            assert_eq!(d, back, "roundtrip changed {v}");
        }
    }
}
