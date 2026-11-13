//! Metadata merge and cascade (spec section 5; decisions G24/G25 in docs/TEST_PLAN.md).
//!
//! Layers are YAML mappings loaded with the strict loader (E2.2) and folded from
//! the LOWEST to the HIGHEST precedence:
//! defaults < project < directories (outermost to nearest) < frontmatter <
//! sidecar < invocation overrides.
//! Mappings merge recursively; any other value (scalar, array, null, or a value
//! of a different kind) replaces what is below it in full. `null` is a value:
//! it replaces and stays present as null. Provenance names, for every leaf of
//! the result, the layer that supplied it, keyed by RFC 6901 JSON Pointer.
//! Which keys are "security values" that must NOT cascade this way is left to
//! the permissions epic (G25).

use std::collections::HashMap;

use crate::diag::Diagnostic;
use crate::yaml::{self, Yaml};

type Res<T> = Result<T, Diagnostic>;

#[derive(Debug, Clone)]
pub struct Layer {
    /// Reported as the diagnostic path and in provenance (usually a file path).
    pub name: String,
    pub text: String,
    /// Line number of the first line of `text` inside its file.
    pub first_line: u32,
}

impl Layer {
    pub fn new(name: &str, text: &str, first_line: u32) -> Layer {
        Layer { name: name.to_string(), text: text.to_string(), first_line }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Merged {
    /// Always a mapping.
    pub value: Yaml,
    /// (JSON Pointer, layer name) for every leaf, in document order. A leaf is
    /// a scalar, null, array, or an empty mapping.
    pub provenance: Vec<(String, String)>,
}

struct MMap {
    entries: Vec<(String, MNode)>,
    index: HashMap<String, usize>,
    /// The last layer that contributed to this mapping (used when it is empty).
    layer: String,
}

enum MNode {
    Leaf(Yaml, String),
    Map(MMap),
}

fn node_from(v: &Yaml, layer: &str) -> MNode {
    match v {
        Yaml::Map(entries) => {
            let mut m = MMap { entries: Vec::new(), index: HashMap::new(), layer: layer.to_string() };
            m.merge(entries, layer);
            MNode::Map(m)
        }
        other => MNode::Leaf(other.clone(), layer.to_string()),
    }
}

impl MMap {
    fn merge(&mut self, incoming: &[(String, Yaml)], layer: &str) {
        self.layer = layer.to_string();
        for (k, v) in incoming {
            match self.index.get(k).copied() {
                Some(idx) => {
                    let both_maps =
                        matches!(self.entries[idx].1, MNode::Map(_)) && matches!(v, Yaml::Map(_));
                    if both_maps {
                        if let (MNode::Map(m), Yaml::Map(inner)) = (&mut self.entries[idx].1, v) {
                            m.merge(inner, layer);
                        }
                    } else {
                        self.entries[idx].1 = node_from(v, layer);
                    }
                }
                None => {
                    self.index.insert(k.clone(), self.entries.len());
                    self.entries.push((k.clone(), node_from(v, layer)));
                }
            }
        }
    }
}

fn escape_pointer_segment(key: &str) -> String {
    key.replace('~', "~0").replace('/', "~1")
}

fn finish(map: &MMap, ptr: &str, prov: &mut Vec<(String, String)>) -> Yaml {
    if map.entries.is_empty() {
        if !ptr.is_empty() {
            prov.push((ptr.to_string(), map.layer.clone()));
        }
        return Yaml::Map(Vec::new());
    }
    let mut out: Vec<(String, Yaml)> = Vec::new();
    for (k, node) in &map.entries {
        let child = format!("{}/{}", ptr, escape_pointer_segment(k));
        let v = match node {
            MNode::Leaf(y, layer) => {
                prov.push((child.clone(), layer.clone()));
                y.clone()
            }
            MNode::Map(m) => finish(m, &child, prov),
        };
        out.push((k.clone(), v));
    }
    Yaml::Map(out)
}

/// Fold `layers` (lowest precedence first) into one mapping.
pub fn merge(layers: &[Layer]) -> Res<Merged> {
    merge_with_top(layers, None)
}

/// `merge`, plus an optional highest layer that is already a value (host inputs in `compile`).
pub fn merge_with_top(layers: &[Layer], top: Option<(&str, &Yaml)>) -> Res<Merged> {
    merge_values(None, layers, top)
}

/// `merge` with optional value layers below (a package manifest) and above (host inputs).
pub fn merge_values(bottom: Option<(&str, &Yaml)>, layers: &[Layer], top: Option<(&str, &Yaml)>) -> Res<Merged> {
    let mut root = MMap { entries: Vec::new(), index: HashMap::new(), layer: String::new() };
    if let Some((name, Yaml::Map(entries))) = bottom {
        root.merge(entries, name);
    }
    for layer in layers {
        let doc = yaml::load(&layer.text, true, layer.first_line).map_err(|mut d| {
            if d.path.is_none() {
                d.path = Some(layer.name.clone());
            }
            d
        })?;
        if let Yaml::Map(entries) = &doc {
            root.merge(entries, &layer.name);
        }
    }
    if let Some((name, Yaml::Map(entries))) = top {
        root.merge(entries, name);
    }
    let mut provenance = Vec::new();
    let value = finish(&root, "", &mut provenance);
    Ok(Merged { value, provenance })
}

/// The six places metadata can come from (all optional).
#[derive(Debug, Clone, Default)]
pub struct Cascade {
    pub defaults: Option<Layer>,
    pub project: Option<Layer>,
    /// `.mdq.yaml` files between the project root (exclusive) and the document's
    /// directory, outermost first.
    pub directories: Vec<Layer>,
    pub frontmatter: Option<Layer>,
    pub sidecar: Option<Layer>,
    pub overrides: Option<Layer>,
}

pub fn cascade(c: &Cascade) -> Res<Merged> {
    cascade_with_top(c, None)
}

/// `cascade` with a value layer above the overrides (see `merge_with_top`).
pub fn cascade_with_top(c: &Cascade, top: Option<(&str, &Yaml)>) -> Res<Merged> {
    cascade_values(c, None, top)
}

/// `cascade` with value layers below and above (see `merge_values`).
pub fn cascade_values(c: &Cascade, bottom: Option<(&str, &Yaml)>, top: Option<(&str, &Yaml)>) -> Res<Merged> {
    let mut layers: Vec<Layer> = Vec::new();
    if let Some(l) = &c.defaults {
        layers.push(l.clone());
    }
    if let Some(l) = &c.project {
        layers.push(l.clone());
    }
    layers.extend(c.directories.iter().cloned());
    if let Some(l) = &c.frontmatter {
        layers.push(l.clone());
    }
    if let Some(l) = &c.sidecar {
        layers.push(l.clone());
    }
    if let Some(l) = &c.overrides {
        layers.push(l.clone());
    }
    merge_values(bottom, &layers, top)
}
