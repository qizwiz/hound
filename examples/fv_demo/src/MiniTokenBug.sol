// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Minimal token with a conservation bug: burn() forgets to decrement totalSupply,
// so the aggregate desyncs from the sum of balances.
contract MiniTokenBug {
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;

    function mint(address to, uint256 amt) external {
        totalSupply += amt;
        balanceOf[to] += amt;
    }

    function burn(uint256 amt) external {
        balanceOf[msg.sender] -= amt; // BUG: missing `totalSupply -= amt;`
    }
}
