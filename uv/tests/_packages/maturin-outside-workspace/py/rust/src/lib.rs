use pyo3::prelude::*;

#[pyfunction]
fn add(a: i64, b: i64) -> i64 {
    outside_core::add(a, b)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(add, m)?)
}
