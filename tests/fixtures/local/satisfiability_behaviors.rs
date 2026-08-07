use super::*;
use anyhow::{bail, Result};
use serde_json::{Map, Value};

pub fn solve_challenge(
    challenge: &Challenge,
    save_solution: &dyn Fn(&Solution) -> Result<()>,
    hyperparameters: &Option<Map<String, Value>>,
) -> Result<()> {
    let mode = hyperparameters
        .as_ref()
        .and_then(|values| values.get("mode"))
        .and_then(Value::as_str)
        .unwrap_or("default");
    let candidate = Solution {
        variables: vec![true; challenge.num_variables],
    };
    match mode {
        "no_callback" => Ok(()),
        "multiple" => {
            save_solution(&Solution {
                variables: vec![false; challenge.num_variables],
            })?;
            save_solution(&candidate)
        }
        "error" => {
            save_solution(&candidate)?;
            bail!("requested test error")
        }
        "panic" => {
            save_solution(&candidate)?;
            panic!("requested test panic")
        }
        "default" => save_solution(&candidate),
        other => bail!("unknown mode {other}"),
    }
}
