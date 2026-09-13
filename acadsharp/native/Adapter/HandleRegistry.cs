using System.Threading;

namespace Viprs.Cad;

// How many handles are alive.
//
// This exists for one test: every malformed input has to leave the count at
// zero, so a failure path that forgets to close something shows up as a
// number rather than as a slow leak nobody measures. The export layer exposes
// it as viprs_acad__test_live_handles(), which is test-only and named so.
//
// Interlocked because two handles may be used from two threads, which the
// header allows even though one handle is single threaded.
public static class HandleRegistry
{
	private static int _documents;
	private static int _decodes;

	public static int LiveDocuments
	{
		get { return Volatile.Read(ref _documents); }
	}

	public static int LiveDecodes
	{
		get { return Volatile.Read(ref _decodes); }
	}

	public static int Live
	{
		get { return LiveDocuments + LiveDecodes; }
	}

	public static void DocumentOpened()
	{
		Interlocked.Increment(ref _documents);
	}

	public static void DocumentClosed()
	{
		Interlocked.Decrement(ref _documents);
	}

	public static void DecodeOpened()
	{
		Interlocked.Increment(ref _decodes);
	}

	public static void DecodeClosed()
	{
		Interlocked.Decrement(ref _decodes);
	}

	// Test-only, and only ever called between scenarios.
	public static void ResetForTest()
	{
		Interlocked.Exchange(ref _documents, 0);
		Interlocked.Exchange(ref _decodes, 0);
	}
}
