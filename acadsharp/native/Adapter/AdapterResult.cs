namespace Viprs.Cad;

// The result codes from include/viprs_acadsharp.h, mirrored once so the
// flattener can return them without every call site writing a literal. The
// header is the contract; this file follows it and never the other way round,
// and tests/test_adapter_stream.py greps both so a drift is a red test.
public static class AdapterResult
{
	public const uint Ok = 0u;
	public const uint InvalidArgument = 1u;
	public const uint UnsupportedFormat = 2u;
	public const uint CorruptInput = 3u;
	public const uint UnsupportedEntity = 4u;
	public const uint OutOfMemory = 5u;
	public const uint Canceled = 6u;
	public const uint InternalError = 7u;
	public const uint AbiMismatch = 8u;
	public const uint LimitExceeded = 9u;

	public static string Name(uint code)
	{
		switch (code)
		{
			case Ok: return "OK";
			case InvalidArgument: return "INVALID_ARGUMENT";
			case UnsupportedFormat: return "UNSUPPORTED_FORMAT";
			case CorruptInput: return "CORRUPT_INPUT";
			case UnsupportedEntity: return "UNSUPPORTED_ENTITY";
			case OutOfMemory: return "OUT_OF_MEMORY";
			case Canceled: return "CANCELED";
			case InternalError: return "INTERNAL_ERROR";
			case AbiMismatch: return "ABI_MISMATCH";
			case LimitExceeded: return "LIMIT_EXCEEDED";
			default: return "UNKNOWN_" + code;
		}
	}
}
