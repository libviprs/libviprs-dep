using System;
using System.Collections.Generic;

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

		public static Primitive Polyline(ulong handle, uint flags, bool closed, double[] points)
		{
			if (points == null || points.Length % 3 != 0)
			{
				throw new ArgumentException("a polyline is a flat run of x, y, z triples");
			}

			return new Primitive
			{
				Type = WireFormat.TypePolyline,
				ItemHandle = handle,
				Flags = flags,
				Counts = new uint[] { (uint)(points.Length / 3), closed ? 1u : 0u },
				Values = points,
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

			List<double> values = new List<double>(
				knots.Length + controlPoints.Length + weights.Length
			);
			values.AddRange(knots);
			values.AddRange(controlPoints);
			values.AddRange(weights);

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
				Values = values.ToArray(),
			};
		}

		public static Primitive Polygon(ulong handle, uint flags, double[] points)
		{
			if (points == null || points.Length % 3 != 0)
			{
				throw new ArgumentException("a polygon is a flat run of x, y, z triples");
			}

			return new Primitive
			{
				Type = WireFormat.TypePolygon,
				ItemHandle = handle,
				Flags = flags,
				Counts = new uint[] { (uint)(points.Length / 3), 0u },
				Values = points,
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
