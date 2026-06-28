// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {MiniTokenBug} from "../src/MiniTokenBug.sol";

// forge-std-free: a minimal cheatcode interface so the test compiles + runs under halmos
// with no external dependencies.
interface Vm { function assume(bool) external; }

contract Conservation {
    Vm constant vm = Vm(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);
    MiniTokenBug t;

    function setUp() public { t = new MiniTokenBug(); }

    // PROPERTY: totalSupply is the running sum of all balances, so any function that mutates a
    // balance must move totalSupply by the same signed amount. burn() forgets totalSupply, so
    // halmos finds a concrete (a, b) with b > 0 that violates it.
    function check_invariant(uint256 a, uint256 b) public {
        vm.assume(a < type(uint128).max);
        vm.assume(b <= a);
        uint256 tsBefore  = t.totalSupply();
        uint256 balBefore = t.balanceOf(address(this));
        t.mint(address(this), a);
        t.burn(b);
        uint256 tsAfter  = t.totalSupply();
        uint256 balAfter = t.balanceOf(address(this));
        assert(int256(tsAfter) - int256(tsBefore) == int256(balAfter) - int256(balBefore));
    }
}
