using System.Text;
using ACadSharp.Entities;

// What a Text record's bytes say: the text the drawing shows, not the text the
// file spells (libviprs-dep#111 and #112).
//
// ACadSharp hands this adapter the strings a DWG holds, decoded against the
// document's code page and otherwise untouched, and two things in them are not
// what a viewer would draw.
//
// MIF escapes. A pre-2007 DWG has no way to hold a character its code page
// lacks, so it spells one `\U+XXXX`: four hex digits, the UTF-16 code unit.
// ACadSharp has no layer that resolves them in either direction, and the
// obvious repair (the string reader every string in a DWG goes through) is the
// wrong place, because layer names, block names and header variables go
// through it too. The text a record 10 carries is where the escape has a
// meaning, so this is where it is resolved.
//
// A multiline attribute. On R2018 an attribute that holds more than one line
// stores its text in an embedded MTEXT and leaves the legacy value empty. The
// legacy value is where an R2004 save puts the same text, so the rule is the
// one a reader of both has to have: the embedded MTEXT when it holds anything,
// the legacy value otherwise.
//
// The rules for an escape, each one the less surprising of two:
//
//  1. Only exactly four hex digits are an escape. `\U+22` is the six characters
//     it looks like. The digits may be either case.
//  2. A surrogate pair spelled as two escapes is the one code point it means.
//     A surrogate on its own is not a character and does not become one: the
//     escape stays as spelled, which is also the answer that does not put an
//     invalid UTF-16 string in front of the wire's UTF-8 encoder.
//  3. A control character (below U+0020), and U+FFFE and U+FFFF, stay as
//     spelled. A NUL in record 10's bytes would end a string in any consumer
//     that treats it as C text, and nothing a drawing means by `\U+0000` is
//     drawable.
//  4. In MTEXT, `\\` is an escaped backslash, so `\\U+2205` is a backslash
//     followed by the letters and not a diameter sign, and `\\\U+2205` is a
//     backslash followed by one. TEXT has no such escape; a backslash in a
//     TEXT is itself.
//  5. Every other MTEXT format code (`\P`, `\f...;`) is left for the consumer,
//     exactly as it was.
namespace Viprs.Cad
{
	internal static class TextForms
	{
		// The value a record 10 carries for a TEXT, MTEXT or ATTRIB.
		public static string Show(TextEntity text)
		{
			AttributeBase attribute = text as AttributeBase;
			if (attribute != null
				&& (attribute.AttributeType == AttributeType.MultiLine
					|| attribute.AttributeType == AttributeType.ConstantMultiLine)
				&& attribute.MText != null
				&& !string.IsNullOrEmpty(attribute.MText.Value))
			{
				return Resolve(attribute.MText.Value, true);
			}

			// An attribute whose type says multiline and whose embedded MTEXT is
			// empty keeps the legacy value, and it is still MTEXT-shaped text.
			bool formatted = attribute != null
				&& (attribute.AttributeType == AttributeType.MultiLine
					|| attribute.AttributeType == AttributeType.ConstantMultiLine);
			return Resolve(text.Value, formatted);
		}

		public static string Show(MText text)
		{
			return Resolve(text.Value, true);
		}

		// `backslashEscapes` is whether `\\` is one backslash, which is MTEXT's
		// grammar and not TEXT's.
		public static string Resolve(string value, bool backslashEscapes)
		{
			if (string.IsNullOrEmpty(value) || value.IndexOf('\\') < 0)
			{
				return value ?? string.Empty;
			}

			StringBuilder sb = null;
			int copied = 0;
			int i = 0;
			while (i < value.Length)
			{
				if (value[i] != '\\')
				{
					i++;
					continue;
				}

				if (backslashEscapes && i + 1 < value.Length && value[i + 1] == '\\')
				{
					i += 2;
					continue;
				}

				int first;
				if (!TryEscape(value, i, out first))
				{
					i++;
					continue;
				}

				int length = 7;
				int cp = first;
				if (first >= 0xD800 && first <= 0xDBFF)
				{
					int second;
					if (TryEscape(value, i + 7, out second) && second >= 0xDC00 && second <= 0xDFFF)
					{
						cp = 0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00);
						length = 14;
					}
					else
					{
						i++;
						continue;
					}
				}
				else if (first >= 0xDC00 && first <= 0xDFFF)
				{
					i++;
					continue;
				}
				else if (first < 0x20 || first == 0xFFFE || first == 0xFFFF)
				{
					i++;
					continue;
				}

				if (sb == null)
				{
					sb = new StringBuilder(value.Length);
				}

				sb.Append(value, copied, i - copied);
				sb.Append(char.ConvertFromUtf32(cp));
				i += length;
				copied = i;
			}

			if (sb == null)
			{
				return value;
			}

			sb.Append(value, copied, value.Length - copied);
			return sb.ToString();
		}

		// `\U+` and exactly four hex digits at `at`.
		private static bool TryEscape(string s, int at, out int unit)
		{
			unit = 0;
			if (at < 0 || at + 7 > s.Length || s[at] != '\\' || s[at + 1] != 'U' || s[at + 2] != '+')
			{
				return false;
			}

			for (int k = 3; k < 7; k++)
			{
				int d = Hex(s[at + k]);
				if (d < 0)
				{
					return false;
				}

				unit = (unit << 4) | d;
			}

			return true;
		}

		private static int Hex(char c)
		{
			if (c >= '0' && c <= '9')
			{
				return c - '0';
			}

			if (c >= 'a' && c <= 'f')
			{
				return c - 'a' + 10;
			}

			if (c >= 'A' && c <= 'F')
			{
				return c - 'A' + 10;
			}

			return -1;
		}
	}
}
