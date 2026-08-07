use super::*;
use anyhow::Result;
use serde_json::{Map, Value};

/// Publish a deliberately slow but feasible schedule.
///
/// This is useful for checking that the offline runner reports raw makespan even
/// when the candidate is worse than TIG's canonical greedy baseline.
pub fn solve_challenge(
    challenge: &Challenge,
    save_solution: &dyn Fn(&Solution) -> Result<()>,
    _hyperparameters: &Option<Map<String, Value>>,
) -> Result<()> {
    let mut job_schedule = Vec::with_capacity(challenge.num_jobs);
    let mut global_time = 0_u32;
    for (product, count) in challenge.jobs_per_product.iter().enumerate() {
        for _ in 0..*count {
            let mut operations = Vec::new();
            for eligible in &challenge.product_processing_times[product] {
                let (&machine, &duration) = eligible
                    .iter()
                    .min_by_key(|(machine, _)| *machine)
                    .expect("every operation has an eligible machine");
                global_time += 10;
                operations.push((machine, global_time));
                global_time += duration;
            }
            job_schedule.push(operations);
        }
    }
    save_solution(&Solution { job_schedule })
}
