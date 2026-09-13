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
			view.MinX = -100.0 - index;
			view.MinY = -50.0 - index;
			view.MaxX = 100.0 + index;
			view.MaxY = 50.0 + index;
			view.ItemCount = _itemsPerView;
			view.Name = index == 0
				? "Model"
				: "Layout" + index.ToString(CultureInfo.InvariantCulture);
			return true;
		}

		// One of every geometry record type first, so a consumer that stops
		// after the first view has still seen all of them, then repeats to
		// reach the requested count.
		public IEnumerable<Primitive> EnumerateView(int index)
		{
			for (uint i = 0; i < _itemsPerView; i++)
			{
				ulong handle = (ulong)((index + 1) * 1000) + i + 1ul;
				double d = i;

				switch (i % 9)
				{
					case 0:
						yield return Primitive.Line(handle, 0u, d, d, 0.0, d + 10.0, d + 20.0, 0.0);
						break;

					case 1:
						yield return Primitive.Polyline(
							handle,
							0u,
							i % 2 == 0,
							new double[] { d, d, 0.0, d + 1.0, d + 2.0, 0.0, d + 3.0, d, 0.0 }
						);
						break;

					case 2:
						yield return Primitive.Arc(
							handle,
							0u,
							d,
							d,
							0.0,
							5.0 + d,
							0.0,
							1.5707963267948966,
							0.0,
							0.0,
							1.0
						);
						break;

					case 3:
						yield return Primitive.Circle(handle, 0u, d, d, 0.0, 3.0 + d, 0.0, 0.0, 1.0);
						break;

					case 4:
						yield return Primitive.Ellipse(
							handle,
							0u,
							d,
							d,
							0.0,
							10.0,
							0.0,
							0.0,
							0.5,
							0.0,
							6.283185307179586,
							0.0,
							0.0,
							1.0
						);
						break;

					case 5:
						yield return Primitive.Spline(
							handle,
							0u,
							3u,
							0u,
							new double[] { 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0 },
							new double[]
							{
								d,
								d,
								0.0,
								d + 1.0,
								d + 3.0,
								0.0,
								d + 4.0,
								d + 3.0,
								0.0,
								d + 5.0,
								d,
								0.0,
							},
							new double[] { 1.0, 0.8, 0.8, 1.0 }
						);
						break;

					case 6:
						yield return Primitive.Polygon(
							handle,
							0u,
							new double[]
							{
								d,
								d,
								0.0,
								d + 4.0,
								d,
								0.0,
								d + 4.0,
								d + 4.0,
								0.0,
								d,
								d + 4.0,
								0.0,
							}
						);
						break;

					case 7:
						yield return Primitive.TextAt(
							handle,
							0u,
							d,
							d,
							0.0,
							2.5,
							0.0,
							"VIPRS synthetic ÄÖÜ " + i.ToString(CultureInfo.InvariantCulture)
						);
						break;

					default:
						yield return Primitive.Warning(
							1u,
							handle,
							"synthetic warning, nothing is wrong with this document"
						);
						break;
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
