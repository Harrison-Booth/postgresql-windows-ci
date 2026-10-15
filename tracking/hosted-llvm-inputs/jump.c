#include "c.h"
__attribute__((noinline)) void jump_from_other_object(void *p) {
 volatile int stack[128];
 stack[0]=99;
 if(stack[0]!=99) abort();
 siglongjmp(*(sigjmp_buf *)p,1);
}
