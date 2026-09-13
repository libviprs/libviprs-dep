using System;

// One record's worth of data, before it becomes bytes.
//
// A document source hands these back and never touches the wire format. That
// is the seam: the source knows drawings, the encoder knows the protocol, and
// neither has to be changed when the other is. The factory methods exist so a
// source cannot get the field order wrong, which is the mistake a bag of
// doubles invites and which no compiler on either side of the boundary would
// catch.
namespace Viprs.Wire
{
	internal sealed class Primitive
	{
		public ushort Type;
		public ulong ItemHandle;

		// Bit 0 is set when the record came from expanding a nested
		// insertion, matching docs/WIRE.md's geometry prologue.
		public uint Flags;

		public double[] Values = Array.Empty<double>();
		public uint[] Counts = Array.Empty<uint>();
		public ulong Count64;
		public string Text;
		public byte[] Raw;

		public static Primitive DocumentBegin(uint viewCount, uint drawingVersion)
		{
			return new Primitive
			{
				Type = WireFormat.TypeDocumentBegin,
				Counts = new uint[] { viewCount, drawingVersion },
			};
		}

		public static Primitive ViewBegin(
			uint index,
			uint kind,
			double minX,
			double minY,
			double maxX,
			double maxY,
			ulong itemCount,
			string name
		)
		{
			return new Primitive
			{
				Type = WireFormat.TypeViewBegin,
				Counts = new uint[] { index, kind },
				Values = new double[] { minX, minY, maxX, maxY },
				Count64 = itemCount,
				Text = name,
			};
		}

		public static Primitive Line(
			ulong handle,
			uint flags,
			double x0,
			double y0,
			double z0,
			double x1,
			double y1,
			double z1
		)
		{
			return new Primitive
			{
				Type = WireFormat.TypeLine,
				ItemHandle = handle,
				Flags = flags,
				Values = new double[] { x0, y0, z0, x1, y1, z1 },
			};
		}

		// Wire version 2's Polyline: a vertex run, a closed flag, the entity's
		// normal, and one bulge per vertex or none at all.
		//
		// `bulges` is either null, meaning every span is straight, or exactly
		// one per vertex. The middle case, a few of them, is not expressible
		// and should not be: a consumer that has to ask which spans an array
		// covers is a consumer reading a length it inferred. The `Spline`
		// record's weight_count is the same rule and the precedent.
		//
		// The values are laid out normal first, then the vertices, then the
		// bulges, which is the order docs/WIRE.md gives and the order the
		// encoder writes them in, so neither has to reorder anything.
		public static Primitive Polyline(
			ulong handle,
			uint flags,
			bool closed,
			double[] points,
			double[] bulges,
			double nx,
			double ny,
			double nz
		)
		{
			return Vertices(
				WireFormat.TypePolyline, handle, flags, closed ? 1u : 0u, points, bulges, nx, ny, nz);
		}

		// Record 9 takes record 4's payload with `closed` always 1. One layout,
		// one reader on the other side, and a hatch loop that turns out to
		// carry a bulge needs no new shape.
		public static Primitive Polygon(
			ulong handle,
			uint flags,
			double[] points,
			double[] bulges,
			double nx,
			double ny,
			double nz
		)
		{
			return Vertices(
				WireFormat.TypePolygon, handle, flags, 1u, points, bulges, nx, ny, nz);
		}

		private static Primitive Vertices(
			ushort type,
			ulong handle,
			uint flags,
			uint closed,
			double[] points,
			double[] bulges,
			double nx,
			double ny,
			double nz
		)
		{
			if (points == null || points.Length % 3 != 0)
			{
				throw new ArgumentException("a vertex run is a flat run of x, y, z triples");
			}

			int n = points.Length / 3;
			bulges = bulges ?? Array.Empty<double>();
			if (bulges.Length != 0 && bulges.Length != n)
			{
				throw new ArgumentException("bulges are either absent or one per vertex");
			}

			double[] values = new double[3 + points.Length + bulges.Length];
			values[0] = nx;
			values[1] = ny;
			values[2] = nz;
			Array.Copy(points, 0, values, 3, points.Length);
			Array.Copy(bulges, 0, values, 3 + points.Length, bulges.Length);

			return new Primitive
			{
				Type = type,
				ItemHandle = handle,
				Flags = flags,
				Counts = new uint[] { (uint)n, closed, (uint)bulges.Length, 0u },
				Values = values,
			};
		}

		public static Primitive Arc(
			ulong handle,
			uint flags,
			double cx,
			double cy,
			double cz,
			double radius,
			double startAngle,
			double endAngle,
			double nx,
			double ny,
			double nz
		)
		{
			return new Primitive
			{
				Type = WireFormat.TypeArc,
				ItemHandle = handle,
				Flags = flags,
				Values = new double[]
				{
					cx,
					cy,
					cz,
					radius,
					startAngle,
					endAngle,
					nx,
					ny,
					nz,
				},
			};
		}

		public static Primitive Circle(
			ulong handle,
			uint flags,
			double cx,
			double cy,
			double cz,
			double radius,
			double nx,
			double ny,
			double nz
		)
		{
			return new Primitive
			{
				Type = WireFormat.TypeCircle,
				ItemHandle = handle,
				Flags = flags,
				Values = new double[] { cx, cy, cz, radius, nx, ny, nz },
			};
		}

		public static Primitive Ellipse(
			ulong handle,
			uint flags,
			double cx,
			double cy,
			double cz,
			double majorX,
			double majorY,
			double majorZ,
			double ratio,
			double startParam,
			double endParam,
			double nx,
			double ny,
			double nz
		)
		{
			return new Primitive
			{
				Type = WireFormat.TypeEllipse,
				ItemHandle = handle,
				Flags = flags,
				Values = new double[]
				{
					cx,
					cy,
					cz,
					majorX,
					majorY,
					majorZ,
					ratio,
					startParam,
					endParam,
					nx,
					ny,
					nz,
				},
			};
		}

		public static Primitive Spline(
			ulong handle,
			uint flags,
			uint degree,
			uint splineFlags,
			double[] knots,
			double[] controlPoints,
			double[] weights
		)
		{
			knots = knots ?? Array.Empty<double>();
			controlPoints = controlPoints ?? Array.Empty<double>();
			weights = weights ?? Array.Empty<double>();
			if (controlPoints.Length % 3 != 0)
			{
				throw new ArgumentException("control points are a flat run of x, y, z triples");
			}

			uint controlCount = (uint)(controlPoints.Length / 3);
			if (weights.Length != 0 && weights.Length != controlCount)
			{
				throw new ArgumentException("weights are either absent or one per control point");
			}

			// One array of the exact size, filled in place, the way Vertices
			// above builds its own. It used to be a List built to that same
			// size, appended to three times and then copied out with ToArray,
			// which is a second array and a second pass over every control
			// point the caller just transformed.
			double[] values = new double[
				knots.Length + controlPoints.Length + weights.Length
			];
			Array.Copy(knots, 0, values, 0, knots.Length);
			Array.Copy(controlPoints, 0, values, knots.Length, controlPoints.Length);
			Array.Copy(
				weights,
				0,
				values,
				knots.Length + controlPoints.Length,
				weights.Length
			);

			return new Primitive
			{
				Type = WireFormat.TypeSpline,
				ItemHandle = handle,
				Flags = flags,
				Counts = new uint[]
				{
					degree,
					splineFlags,
					(uint)knots.Length,
					controlCount,
					(uint)weights.Length,
					0u,
				},
				Values = values,
			};
		}

		public static Primitive TextAt(
			ulong handle,
			uint flags,
			double x,
			double y,
			double z,
			double height,
			double rotation,
			string text
		)
		{
			return new Primitive
			{
				Type = WireFormat.TypeText,
				ItemHandle = handle,
				Flags = flags,
				Values = new double[] { x, y, z, height, rotation },
				Text = text,
			};
		}

		public static Primitive Warning(uint code, ulong handle, string message)
		{
			return new Primitive
			{
				Type = WireFormat.TypeWarning,
				ItemHandle = handle,
				Counts = new uint[] { code },
				Text = message,
			};
		}

		public static Primitive ViewEnd(uint index, ulong recordCount)
		{
			return new Primitive
			{
				Type = WireFormat.TypeViewEnd,
				Counts = new uint[] { index },
				Count64 = recordCount,
			};
		}

		public static Primitive DocumentEnd(ulong totalRecords, ulong warningCount)
		{
			return new Primitive
			{
				Type = WireFormat.TypeDocumentEnd,
				Count64 = totalRecords,
				Counts = Array.Empty<uint>(),
				Values = Array.Empty<double>(),
				Raw = null,
				Text = null,
				ItemHandle = warningCount,
			};
		}

		// A record from a wire version that does not exist yet. Its payload
		// length deliberately matches no other record, so a consumer that
		// skipped by a table of known sizes lands mid-record and fails
		// visibly rather than reading the next one at the wrong offset.
		public static Primitive ForwardProbe()
		{
			return new Primitive
			{
				Type = WireFormat.ForwardProbe,
				Raw = new byte[]
				{
					0x76,
					0x32,
					0x2D,
					0x6F,
					0x6E,
					0x6C,
					0x79,
					0x2D,
					0x72,
					0x65,
					0x63,
					0x6F,
					0x72,
				},
			};
		}
	}
}
