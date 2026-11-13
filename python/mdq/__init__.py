"""MDQ: extended Markdown processor. Logic lives in the Rust core."""
from mdq._native import (AmbiguousReferenceError, CalcError, DuplicateIdError, ImportCycleError, InvalidSyntaxError,
                         MdqError, MetadataError, MissingImportError, ReferenceFailure, ResourceLimitError,
                         SecurityDeniedError, SourceError, SourceFetchFailedError, SourceInvalidError, SourceTypeError,
                         SourceUnresolvedError, UndefinedVariableError, UnresolvedReferenceError,
                         UnsupportedFeatureError, version_info)
from mdq._api import (CalcBlock, CalcValue, DeclaredValue, DirectoryFS, Evaluation, MemoryFS, PackageFS, Result,
                      SourceDeclaration, compile, evaluate, test_source, validate_sources)

__version__: str = version_info()["package"]
__all__ = ["MdqError", "version_info", "__version__", "compile", "evaluate", "MemoryFS", "DirectoryFS",
           "Result", "Evaluation", "CalcBlock", "CalcValue", "DeclaredValue",
           "PackageFS", "SourceDeclaration", "validate_sources", "test_source",
           "ReferenceFailure", "SourceError", "UndefinedVariableError", "MetadataError", "InvalidSyntaxError",
           "UnsupportedFeatureError", "SecurityDeniedError", "CalcError", "ResourceLimitError", "MissingImportError",
           "ImportCycleError", "AmbiguousReferenceError", "UnresolvedReferenceError", "DuplicateIdError",
           "SourceUnresolvedError", "SourceInvalidError", "SourceFetchFailedError", "SourceTypeError"]
