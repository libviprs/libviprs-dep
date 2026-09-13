using System.Globalization;
using Viprs.Wire;

// The name of a record type, for the one message in the shim that has to say
// one out loud.
//
// It sat in CanonicalDump beside two hundred lines of expectation formatting,
// a StringBuilder and a stream diff, all of which is test infrastructure and
// none of which a published library has any use for. The formatting moved to
// the fixture generator; this is the twenty lines the shim actually needs, and
// the generator uses the same twenty so the warning and the dump cannot end up
// calling the same record two different things.
namespace Viprs.Cad
{
	internal static class RecordName
	{
		public static string Of(ushort type)
		{
			switch (type)
			{
				case WireFormat.TypeDocumentBegin: return "DocumentBegin";
				case WireFormat.TypeViewBegin: return "ViewBegin";
				case WireFormat.TypeLine: return "Line";
				case WireFormat.TypePolyline: return "Polyline";
				case WireFormat.TypeArc: return "Arc";
				case WireFormat.TypeCircle: return "Circle";
				case WireFormat.TypeEllipse: return "Ellipse";
				case WireFormat.TypeSpline: return "Spline";
				case WireFormat.TypePolygon: return "Polygon";
				case WireFormat.TypeText: return "Text";
				case WireFormat.TypeWarning: return "Warning";
				case WireFormat.TypeViewEnd: return "ViewEnd";
				case WireFormat.TypeDocumentEnd: return "DocumentEnd";

				// The forward probe, and anything a later wire version adds.
				default: return "Type" + type.ToString(CultureInfo.InvariantCulture);
			}
		}
	}
}
