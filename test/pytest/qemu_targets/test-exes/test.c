#include <assert.h>

// Supports tests for:
//
// * ARMQemuTarget.get_arg():
//
//   One test will break on a call to this function, and ensure that
//   in_* arguments are all set correctly.
//
//
// * ARMQemuTarget.set_arg():
//
//   A second test will break on a call to this function, then change
//   the arguments to something else. It'll then resume execution,
//   break on breakpoint_check_arguments_check_return(), and make sure
//   the "new" return value is based on the arguments set with
//   'set_arg' instead of the original.
int arguments_check(int in_r0, int in_r1, int in_r2, int in_r3, int on_stack1, int on_stack2)
{
    return 0
        + (in_r0)
        + (in_r1     << 4)
        + (in_r2     << 8)
        + (in_r3     << 12)
        + (on_stack1 << 16)
        + (on_stack2 << 20)
        ;
}

void breakpoint_check_arguments_check_return()
{
}


// Supports tests for:
//
// * ARMQemuTarget.execute_return():
//
//   We will put a breakpoint on both this function and
//   breakpoint_check_twelve(), then run execute_return(). On the next
//   breakpoint hit, we will check r0 and make sure it's the value
//   passed to execute_return() rather than 12.
//
//   (A second test will check with ret_value=None, to ensure it is
//   unchanged.)
int return_12()
{
    return 12;
}

void breakpoint_check_twelve(int twelve)
{
}




int main()
{
    int ans = arguments_check(1, 2, 3, 4, 5, 6);
    breakpoint_check_arguments_check_return();

    int twelve = return_12();
    breakpoint_check_twelve(twelve);

    return 0;
}
