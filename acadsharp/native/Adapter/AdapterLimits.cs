using System;

namespace Viprs.Cad;

// The managed mirror of viprs_acad_limits_v1.
//
// The header's rule is that a null limits pointer means every default and a
// zero field means the default for that field, so a caller can set one bound
// without knowing the rest. That rule lives here rather than in the export
// wrapper, because the flattener is what enforces the bounds and a default it
// never saw is a bound nobody applied.
public sealed class AdapterLimits
{
	// Defaults. Each one is a refusal a host can raise, not a promise about
	// what DWG contains, and each is picked to be well clear of the corpus in
	// tests/fixtures while still refusing a file built to exhaust memory.
	public const ulong DefaultMaxInputBytes = 512UL * 1024UL * 1024UL;
	public const ulong DefaultMaxEntities = 20UL * 1000UL * 1000UL;
	public const ulong DefaultMaxStringBytes = 1UL * 1024UL * 1024UL;
	public const ulong DefaultMaxPolylinePoints = 1UL * 1000UL * 1000UL;
	public const uint DefaultMaxBlockDepth = 32u;
	public const ulong DefaultMaxOutputBytes = 4UL * 1024UL * 1024UL * 1024UL;

	public ulong MaxInputBytes = DefaultMaxInputBytes;
	public ulong MaxEntities = DefaultMaxEntities;
	public ulong MaxStringBytes = DefaultMaxStringBytes;
	public ulong MaxPolylinePoints = DefaultMaxPolylinePoints;
	public uint MaxBlockDepth = DefaultMaxBlockDepth;
	public ulong MaxOutputBytes = DefaultMaxOutputBytes;

	public static AdapterLimits Defaults()
	{
		return new AdapterLimits();
	}

	// Zero means "the default for that field", which is the header's rule.
	public static AdapterLimits FromFields(
		ulong maxInputBytes,
		ulong maxEntities,
		ulong maxStringBytes,
		ulong maxPolylinePoints,
		uint maxBlockDepth,
		ulong maxOutputBytes)
	{
		AdapterLimits l = new AdapterLimits();
		if (maxInputBytes != 0) { l.MaxInputBytes = maxInputBytes; }
		if (maxEntities != 0) { l.MaxEntities = maxEntities; }
		if (maxStringBytes != 0) { l.MaxStringBytes = maxStringBytes; }
		if (maxPolylinePoints != 0) { l.MaxPolylinePoints = maxPolylinePoints; }
		if (maxBlockDepth != 0) { l.MaxBlockDepth = maxBlockDepth; }
		if (maxOutputBytes != 0) { l.MaxOutputBytes = maxOutputBytes; }
		return l;
	}

	public AdapterLimits Clone()
	{
		return FromFields(
			this.MaxInputBytes,
			this.MaxEntities,
			this.MaxStringBytes,
			this.MaxPolylinePoints,
			this.MaxBlockDepth,
			this.MaxOutputBytes);
	}

	public override string ToString()
	{
		return string.Format(
			System.Globalization.CultureInfo.InvariantCulture,
			"input={0} entities={1} string={2} polyline={3} depth={4} output={5}",
			this.MaxInputBytes,
			this.MaxEntities,
			this.MaxStringBytes,
			this.MaxPolylinePoints,
			this.MaxBlockDepth,
			this.MaxOutputBytes);
	}
}

// Raised inside the walk and turned into a result code at the boundary. It
// carries the bound that tripped so an operator can tell which one to raise,
// which a bare LIMIT_EXCEEDED never tells anyone.
public sealed class AdapterLimitException : Exception
{
	public string Bound { get; }

	public AdapterLimitException(string bound, string message) : base(message)
	{
		this.Bound = bound;
	}
}

public sealed class AdapterCanceledException : Exception
{
	public AdapterCanceledException() : base("the caller's cancel flag was set")
	{
	}
}
