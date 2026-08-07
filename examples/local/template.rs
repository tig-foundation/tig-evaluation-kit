use super::*;
use anyhow::{bail, Result};
use serde_json::{Map, Value};

/// Copy this file and implement the same callback contract used by TIG algorithms.
pub fn solve_challenge(
    _challenge: &Challenge,
    _save_solution: &dyn Fn(&Solution) -> Result<()>,
    _hyperparameters: &Option<Map<String, Value>>,
) -> Result<()> {
    bail!("implement this local algorithm")
}
