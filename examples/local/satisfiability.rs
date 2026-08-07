use super::*;
use anyhow::Result;
use serde_json::{Map, Value};

/// Minimal example: publish one structurally valid assignment.
pub fn solve_challenge(
    challenge: &Challenge,
    save_solution: &dyn Fn(&Solution) -> Result<()>,
    _hyperparameters: &Option<Map<String, Value>>,
) -> Result<()> {
    save_solution(&Solution {
        variables: vec![true; challenge.num_variables],
    })
}
