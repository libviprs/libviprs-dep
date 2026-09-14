// The entity kinds somebody looked at and decided against.
//
// Every kind this version does not flatten used to leave the same warning:
// code 100, "X is not a primitive this version flattens". That sentence is
// true of two completely different situations and a consumer cannot tell them
// apart, because docs/WIRE.md forbids parsing the message and 100 was the only
// number either of them carried. "Nobody has got to MLINE yet" and "no decoder
// built on this reader will ever render a 3DSOLID" are different facts, and a
// consumer deciding whether to show a placeholder, warn a user, or go and find
// another tool wants the second one.
//
// So the kinds with a decision behind them are listed here, with the code and
// the sentence each one emits, and Flattener's default arm asks this table
// before it falls back to 100. docs/adr/0002-what-this-decoder-refuses.md is
// the reasoning and carries the condition that would reopen each row;
// tests/test_refusal_decisions.py holds the ADR, this file and WIRE.md's
// warning-code table to the same set of kinds and the same codes, with no
// .NET present.
//
// Keyed on ObjectName rather than on the CLR type on purpose. That is the
// string the wire already carries, every expectation dump in
// tests/expectations names these kinds with it, and a case per type would put
// eight ACadSharp class names into a switch nothing in this repository's gate
// compiles.
//
// Every row here is on code 109, and that is now true rather than nearly true.
// WIPEOUT used to sit in this table on 100, as the one row that was deferred
// work rather than a decision, and it is gone because the work is done: its
// boundary lowers to the Polygon record that already existed, with warning 112
// beside it saying that the polygon masks. Flatten.Masks.cs is where that
// lives now.
//
// So the table says one thing again. A kind is here when the answer will not
// change by waiting, the code beside it says so, and a kind nobody has got to
// yet falls through to 100 with no row at all. If a later round wants to defer
// something in here again, the thing to keep is that a consumer can still tell
// the two apart from the number alone, which is the whole of what 109 bought.
namespace Viprs.Cad
{
	internal static class RefusedKinds
	{
		public static bool TryGet(string objectName, out uint code, out string reason)
		{
			switch (objectName)
			{
				case "3DSOLID":
					code = WarningCodes.EntityRefusedByDesign;
					reason =
						"3DSOLID keeps its geometry as an embedded ACIS stream, which is a "
						+ "boundary representation rather than a tessellation and which nothing "
						+ "on this boundary evaluates, so there is no outline here to flatten";
					return true;

				case "REGION":
					code = WarningCodes.EntityRefusedByDesign;
					reason =
						"REGION keeps its geometry as an embedded ACIS stream, the same as "
						+ "3DSOLID, so there is no outline here to flatten and producing one "
						+ "means evaluating a proprietary boundary representation";
					return true;

				case "SHAPE":
					code = WarningCodes.EntityRefusedByDesign;
					reason =
						"SHAPE draws a glyph out of an external SHX file and carries only the "
						+ "placement and the name, and this decoder opens no path a drawing "
						+ "names, so the outline is not in this file and is not fetched";
					return true;

				case "IMAGE":
					code = WarningCodes.EntityRefusedByDesign;
					reason =
						"IMAGE shows an external raster the drawing only names, and this decoder "
						+ "opens no path a drawing names, so what it displays is not in this file";
					return true;

				case "PDFUNDERLAY":
					code = WarningCodes.EntityRefusedByDesign;
					reason =
						"PDFUNDERLAY shows an external PDF the drawing only names, and this "
						+ "decoder opens no path a drawing names, so what it displays is not in "
						+ "this file";
					return true;

				case "RAY":
					code = WarningCodes.EntityRefusedByDesign;
					reason =
						"RAY runs from its base point in one direction forever and every record "
						+ "wire version 2 defines is bounded, so it is refused rather than "
						+ "clipped to the drawing's extents and passed off as a line";
					return true;

				case "XLINE":
					code = WarningCodes.EntityRefusedByDesign;
					reason =
						"XLINE runs both ways from its base point forever and every record wire "
						+ "version 2 defines is bounded, so it is refused rather than clipped to "
						+ "the drawing's extents and passed off as a line";
					return true;

				default:
					code = 0u;
					reason = null;
					return false;
			}
		}
	}
}
