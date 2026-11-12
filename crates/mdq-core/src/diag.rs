//! Structured diagnostics (spec: extended-markdown.md section 11).

/// The stable, machine-readable diagnostic codes.
///
/// `as_str` uses an exhaustive `match`, so adding a variant without giving it
/// a string is a compile error.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Code {
    UndefinedVariable,
    MissingImport,
    ImportCycle,
    AmbiguousReference,
    UnresolvedReference,
    DuplicateId,
    MetadataError,
    InvalidSyntax,
    UnsupportedFeature,
    SecurityDenied,
    CalcError,
    ResourceLimit,
    SourceUnresolved,
    SourceInvalid,
    SourceFetchFailed,
    SourceTypeError,
}

impl Code {
    pub const ALL: [Code; 16] = [
        Code::UndefinedVariable,
        Code::MissingImport,
        Code::ImportCycle,
        Code::AmbiguousReference,
        Code::UnresolvedReference,
        Code::DuplicateId,
        Code::MetadataError,
        Code::InvalidSyntax,
        Code::UnsupportedFeature,
        Code::SecurityDenied,
        Code::CalcError,
        Code::ResourceLimit,
        Code::SourceUnresolved,
        Code::SourceInvalid,
        Code::SourceFetchFailed,
        Code::SourceTypeError,
    ];

    pub fn as_str(self) -> &'static str {
        match self {
            Code::UndefinedVariable => "MDQ_UNDEFINED_VARIABLE",
            Code::MissingImport => "MDQ_MISSING_IMPORT",
            Code::ImportCycle => "MDQ_IMPORT_CYCLE",
            Code::AmbiguousReference => "MDQ_AMBIGUOUS_REFERENCE",
            Code::UnresolvedReference => "MDQ_UNRESOLVED_REFERENCE",
            Code::DuplicateId => "MDQ_DUPLICATE_ID",
            Code::MetadataError => "MDQ_METADATA_ERROR",
            Code::InvalidSyntax => "MDQ_INVALID_SYNTAX",
            Code::UnsupportedFeature => "MDQ_UNSUPPORTED_FEATURE",
            Code::SecurityDenied => "MDQ_SECURITY_DENIED",
            Code::CalcError => "MDQ_CALC_ERROR",
            Code::ResourceLimit => "MDQ_RESOURCE_LIMIT",
            Code::SourceUnresolved => "MDQ_SOURCE_UNRESOLVED",
            Code::SourceInvalid => "MDQ_SOURCE_INVALID",
            Code::SourceFetchFailed => "MDQ_SOURCE_FETCH_FAILED",
            Code::SourceTypeError => "MDQ_SOURCE_TYPE_ERROR",
        }
    }
}

/// One structured diagnostic: what went wrong and where.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Diagnostic {
    pub code: Code,
    pub message: String,
    pub path: Option<String>,
    pub line: Option<u32>,
    pub column: Option<u32>,
}

impl Diagnostic {
    pub fn new(code: Code, message: impl Into<String>) -> Self {
        Diagnostic {
            code,
            message: message.into(),
            path: None,
            line: None,
            column: None,
        }
    }

    /// Attach a line and column only (the caller adds the file path later).
    pub fn at_position(mut self, line: u32, column: u32) -> Self {
        self.line = Some(line);
        self.column = Some(column);
        self
    }

    /// Attach a source location (builder style).
    pub fn at(mut self, path: impl Into<String>, line: u32, column: u32) -> Self {
        self.path = Some(path.into());
        self.line = Some(line);
        self.column = Some(column);
        self
    }
}
