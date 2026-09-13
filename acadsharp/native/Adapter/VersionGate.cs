using System.Globalization;
using Viprs.Abi;

// The six-byte DWG version signature, checked before anything parses.
//
// The pinned ACadSharp reads a bounded range of AC10xx codes, and
// `AbiConstants` is where that range is written down. Handed a file older
// than the low end its reader gets far enough in to fail somewhere obscure,
// and the caller is then told the file is corrupt when the truth is that it
// is fine and older than this build. Those are different problems for
// whoever is holding the file, so the signature decides before the reader is
// ever constructed.
//
// The two constants below are aliases, not a second copy. The capabilities
// call answers out of `AbiConstants`, and the gate that decides whether a
// file is opened at all has to enforce the same numbers the library
// advertises. They used to be two pairs of literals, which is two places to
// bump and one of them silently wrong.
namespace Viprs.Cad
{
	internal static class VersionGate
	{
		public const uint MinVersion = AbiConstants.DwgVersionMin;
		public const uint MaxVersion = AbiConstants.DwgVersionMax;

		// The six bytes every DWG opens with, as a number, or 0 when they are
		// not a version signature at all.
		public static uint Parse(byte[] head, int length)
		{
			if (head == null || length < 6)
			{
				return 0u;
			}

			if (head[0] != (byte)'A' || head[1] != (byte)'C')
			{
				return 0u;
			}

			uint value = 0u;
			for (int i = 2; i < 6; i++)
			{
				byte b = head[i];
				if (b < (byte)'0' || b > (byte)'9')
				{
					return 0u;
				}

				value = (value * 10u) + (uint)(b - (byte)'0');
			}

			return value;
		}

		public static bool IsSupported(uint version)
		{
			return version >= MinVersion && version <= MaxVersion;
		}

		public static string Describe(uint version)
		{
			return version == 0u
				? "no DWG version signature"
				: "AC" + version.ToString(CultureInfo.InvariantCulture);
		}
	}
}
