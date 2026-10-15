#include "c.h"
#undef printf
extern void jump_from_other_object(void *);
int main(void) {
 sigjmp_buf b;
 volatile int guard=31415;
 for(int i=0;i<1000;i++) {
  if(sigsetjmp(b,0)==0) jump_from_other_object(b);
  else if(guard!=31415) return 2;
 }
 printf("1000 nonlocal jumps passed; buffer=%zu align=%zu\n",sizeof(b),(size_t)_Alignof(sigjmp_buf));
 return 0;
}
