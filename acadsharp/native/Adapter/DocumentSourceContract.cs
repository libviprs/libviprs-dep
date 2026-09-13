using System;
using System.Collections.Generic;

namespace Viprs.Cad;

// What the export layer needs from whatever is behind a viprs_cad_handle.
//
// Written here against the semantics of include/viprs_acadsharp.h because the
// branch this work started from carries the frozen header and not yet the
// export wrappers that consume it. When the ABI lane's Sources/IDocumentSource.cs
// lands, this file goes and AcadSharpSource.cs implements that one instead:
// the shape below is the shape the header forces either way, since view_count,
// view_info_v1 and decode_begin are the only three things an export can ask a
// document for.
public interface ICancelFlag
{
	// Read between batches, never written, and the caller may change it from
	// another thread. v1 has no callbacks into the caller, so this is a poll.
	bool IsSet { get; }
}

public sealed class NeverCanceled : ICancelFlag
{
	public static readonly NeverCanceled Instance = new NeverCanceled();

	public bool IsSet
	{
		get { return false; }
	}
}

// A flag the caller can flip, which is what the export binds a
// `const uint32_t *cancel_flag` to.
public sealed class MutableCancelFlag : ICancelFlag
{
	private volatile bool _set;

	public bool IsSet
	{
		get { return this._set; }
	}

	public void Set()
	{
		this._set = true;
	}
}

// viprs_view_info_v1, minus struct_size and struct_version, which belong to
// the marshalling and not to the document.
public sealed class ViewInfo
{
	public const uint KindModel = 0u;
	public const uint KindLayout = 1u;
	public const uint KindUnknown = 2u;

	public uint Index;
	public uint Kind;
	public double MinX;
	public double MinY;
	public double MaxX;
	public double MaxY;
	public ulong EntityCount;
	public string Name = string.Empty;
}

public interface IDocumentSource : IDisposable
{
	int ViewCount { get; }

	ViewInfo GetView(int index);

	// Lazy on purpose. The decode is a walk over a document that is already
	// resident, and materialising the flattened stream would put a second
	// copy of the drawing in memory for no one's benefit. Everything that
	// consumes this pulls one record at a time.
	IEnumerable<Record> Decode(int viewIndex, AdapterLimits limits, ICancelFlag cancel);
}
