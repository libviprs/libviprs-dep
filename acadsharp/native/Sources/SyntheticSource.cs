using System;
using System.Collections.Generic;
using System.Globalization;
using Viprs.Wire;

// A document this library makes up, selected by a magic byte sequence.
//
// It exists so that every entry point and every record type is reachable from
// a conformance consumer on this issue alone, before any adapter exists. That
// is worth more than it sounds: a parser exercised only against buffers a test
// assembled by hand has never been pointed at the encoder that will actually
// feed it, and the two disagreeing is the bug neither side's tests can see.
//
// The layout after the magic is eight optional bytes, two little-endian
// uint32 values: how many views, and how many geometry primitives per view.
// Both are clamped, because this is reachable across a published ABI and an
// input that says four billion views is an input.
namespace Viprs.Sources
{
	internal sealed class SyntheticSource : IDocumentSource
	{
		// "VIPRSSYN". Written as bytes rather than as a string so nothing
		// here depends on an encoding.
		private static readonly byte[] MagicBytes = new byte[]
		{
			0x56,
			0x49,
			0x50,
			0x52,
			0x53,
			0x53,
			0x59,
			0x4E,
		};

		public const int MagicLength = 8;

		private const uint DefaultViews = 2;
		private const uint DefaultItemsPerView = 9;
		private const uint MaxViews = 64;
		private const uint MaxItemsPerView = 4096;

		private readonly uint _views;
		private readonly uint _itemsPerView;

		private SyntheticSource(uint views, uint itemsPerView)
		{
			_views = views;
			_itemsPerView = itemsPerView;
		}

		public static bool Matches(byte[] data)
		{
			if (data == null || data.Length < MagicLength)
			{
				return false;
			}

			for (int i = 0; i < MagicLength; i++)
			{
				if (data[i] != MagicBytes[i])
				{
					return false;
				}
			}

			return true;
		}

		public static SyntheticSource Open(byte[] data)
		{
			uint views = DefaultViews;
			uint items = DefaultItemsPerView;

			if (data.Length >= MagicLength + 8)
			{
				views = ReadU32(data, MagicLength);
				items = ReadU32(data, MagicLength + 4);
			}

			return new SyntheticSource(Clamp(views, 1u, MaxViews), Clamp(items, 1u, MaxItemsPerView));
		}

		public uint DrawingVersion
		{
			get { return 1032u; }
		}

		public int ViewCount
		{
			get { return (int)_views; }
		}

		public bool TryGetView(int index, out SourceView view)
		{
			view = default(SourceView);
			if (index < 0 || index >= _views)
			{
				return false;
			}

			view.Kind = index == 0 ? 0u : 1u;
			// Four different numbers, none of them round and none of them a
			// plausible stand-in for another, for the same reason the record
			// payloads use probes: equal or symmetric extents hide a swap.
			view.MinX = -100.25 - index;
			view.MinY = -50.5 - index;
			view.MaxX = 100.75 + index;
			view.MaxY = 50.125 + index;
			view.ItemCount = _itemsPerView;
			view.Name = index == 0
				? "Model"
				: "Layout" + index.ToString(CultureInfo.InvariantCulture);
			return true;
		}

		// Every scalar in this document is a placement probe, not geometry.
		//
		// The k-th double in a record's payload, counting from zero after the
		// geometry prologue, is 100 * type + k + 0.25, and the item handle is
		// 1000000 + type. So no two fields of a record hold the same number,
		// no field holds zero, and no field holds a value that would be
		// plausible in its neighbour's place. That is the only way a consumer
		// can prove it reads each field at the offset docs/WIRE.md gives it:
		// against a document full of zeroes and repeats, swapping an arc's
		// radius with its start angle changes nothing anybody can see.
		//
		// docs/ABI.md documents the rule, because a conformance consumer
		// asserts on these numbers and that makes them part of the contract.
		public const double ProbeFraction = 0.25;
		public const ulong ProbeHandleBase = 1000000ul;
		public const string ProbeText = "VIPRS-TEXT-PROBE-\u00C4";
		public const string ProbeWarning = "VIPRS-WARNING-PROBE";
		public const uint ProbeWarningCode = 1100u;
		public const uint ProbeSplineDegree = 3u;

		public static double Probe(ushort type, int k)
		{
			return (100.0 * type) + k + ProbeFraction;
		}

		public static ulong ProbeHandle(ushort type)
		{
			return ProbeHandleBase + type;
		}

		private static double[] Probes(ushort type, int count)
		{
			double[] values = new double[count];
			for (int k = 0; k < count; k++)
			{
				values[k] = Probe(type, k);
			}

			return values;
		}

		// One of every record type in the first nine, so a consumer that
		// stops after nine primitives has still seen all of them, then
		// repeats. The variable-length records change length as they repeat,
		// which is what exercises the padding and the length invariants at
		// every residue rather than only at the convenient one.
		// The cancel flag is ignored here on purpose: this source yields a
		// primitive per item with nothing in between, so the batch writer
		// comes back to the export between every one of them and the flag is
		// read there.
		public IEnumerable<Primitive> EnumerateView(int index, Func<bool> canceled)
		{
			for (uint i = 0; i < _itemsPerView; i++)
			{
				switch (i % 9)
				{
					case 0:
					{
						double[] v = Probes(WireFormat.TypeLine, 6);
						yield return Primitive.Line(
							ProbeHandle(WireFormat.TypeLine),
							0u,
							v[0],
							v[1],
							v[2],
							v[3],
							v[4],
							v[5]
						);
						break;
					}

					case 1:
					{
						int points = 3 + (int)(i % 4);
						yield return Primitive.Polyline(
							ProbeHandle(WireFormat.TypePolyline),
							0u,
							i % 2 == 0,
							Probes(WireFormat.TypePolyline, points * 3)
						);
						break;
					}

					case 2:
					{
						double[] v = Probes(WireFormat.TypeArc, 9);
						yield return Primitive.Arc(
							ProbeHandle(WireFormat.TypeArc),
							0u,
							v[0],
							v[1],
							v[2],
							v[3],
							v[4],
							v[5],
							v[6],
							v[7],
							v[8]
						);
						break;
					}

					case 3:
					{
						double[] v = Probes(WireFormat.TypeCircle, 7);
						yield return Primitive.Circle(
							ProbeHandle(WireFormat.TypeCircle),
							0u,
							v[0],
							v[1],
							v[2],
							v[3],
							v[4],
							v[5],
							v[6]
						);
						break;
					}

					case 4:
					{
						double[] v = Probes(WireFormat.TypeEllipse, 12);
						yield return Primitive.Ellipse(
							ProbeHandle(WireFormat.TypeEllipse),
							0u,
							v[0],
							v[1],
							v[2],
							v[3],
							v[4],
							v[5],
							v[6],
							v[7],
							v[8],
							v[9],
							v[10],
							v[11]
						);
						break;
					}

					case 5:
					{
						// knots, then control points, then weights, numbered
						// as one run so a consumer can tell the three apart by
						// where they start rather than by their values.
						int knots = 8;
						int controls = 4;
						int weights = 4;
						double[] all = Probes(
							WireFormat.TypeSpline,
							knots + (controls * 3) + weights
						);
						double[] k = new double[knots];
						double[] c = new double[controls * 3];
						double[] w = new double[weights];
						Array.Copy(all, 0, k, 0, knots);
						Array.Copy(all, knots, c, 0, controls * 3);
						Array.Copy(all, knots + (controls * 3), w, 0, weights);
						yield return Primitive.Spline(
							ProbeHandle(WireFormat.TypeSpline),
							0u,
							ProbeSplineDegree,
							0u,
							k,
							c,
							w
						);
						break;
					}

					case 6:
					{
						int points = 3 + (int)(i % 3);
						yield return Primitive.Polygon(
							ProbeHandle(WireFormat.TypePolygon),
							0u,
							Probes(WireFormat.TypePolygon, points * 3)
						);
						break;
					}

					case 7:
					{
						double[] v = Probes(WireFormat.TypeText, 5);
						// The probe string is 19 bytes, which is not a
						// multiple of four, and the repeat adds nought to
						// three more, so the padding is exercised at every
						// residue rather than only at the one that is zero.
						yield return Primitive.TextAt(
							ProbeHandle(WireFormat.TypeText),
							0u,
							v[0],
							v[1],
							v[2],
							v[3],
							v[4],
							ProbeText + new string('z', (int)(i % 4))
						);
						break;
					}

					default:
					{
						yield return Primitive.Warning(
							ProbeWarningCode,
							ProbeHandle(WireFormat.TypeWarning),
							ProbeWarning
						);
						break;
					}
				}
			}
		}

		public void Dispose() { }

		private static uint ReadU32(byte[] data, int offset)
		{
			return (uint)data[offset]
				| ((uint)data[offset + 1] << 8)
				| ((uint)data[offset + 2] << 16)
				| ((uint)data[offset + 3] << 24);
		}

		private static uint Clamp(uint value, uint low, uint high)
		{
			if (value < low)
			{
				return low;
			}

			return value > high ? high : value;
		}
	}
}
