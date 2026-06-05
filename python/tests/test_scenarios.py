import pytest
import os
import csv

from reference.generate_scenarios import generate_scenario
from reference.interface import OrderType

def test_generate_warmup_scenario(tmp_path):
    output_dir = tmp_path / "data"
    output_dir.mkdir()
    
    generate_scenario("warmup", 100, seed=42, distribution={
        'limit': 0.8,
        'market': 0.2
    }, output_dir=str(output_dir))
    
    orders_file = output_dir / "warmup_orders.csv"
    expected_file = output_dir / "warmup_expected.csv"
    
    assert orders_file.exists()
    assert expected_file.exists()
    
    with open(orders_file, "r") as f:
        reader = csv.DictReader(f)
        orders = list(reader)
        assert len(orders) == 100

def test_determinism(tmp_path):
    output_dir1 = tmp_path / "data1"
    output_dir1.mkdir()
    output_dir2 = tmp_path / "data2"
    output_dir2.mkdir()
    
    generate_scenario("test1", 50, seed=123, distribution={'limit': 1.0}, output_dir=str(output_dir1))
    generate_scenario("test2", 50, seed=123, distribution={'limit': 1.0}, output_dir=str(output_dir2))
    
    with open(output_dir1 / "test1_orders.csv", "r") as f1, open(output_dir2 / "test2_orders.csv", "r") as f2:
        assert f1.read() == f2.read()
