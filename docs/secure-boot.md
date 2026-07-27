# Secure boot status

**Result for the unit examined: the secure-boot fuse is not burned.** The SoC
should accept an unsigned bootloader, which is what makes a mainline U-Boot port
worth attempting at all.

This page records how that was determined so it can be repeated on another box,
because the answer gates every flashing decision and guessing at it is how
people brick hardware.

## Why the obvious check does not work

The stock bootloader has an `otp_gettrustzonestat` command. On this build it is
a stub:

```asm
c5d7f0:  push {lr}
         ...                    ; set up a log message
c5d818:  bl   0xc3594c          ; log(...)
c5d81c:  mvn  r0, #0            ; return -1, unconditionally
c5d824:  pop  {pc}
```

It never reads anything. `Get TEE status failed, ret: 0xffffffff` is what it
always prints, on every board, and says nothing about the chip.

## Where the flag actually lives

`otp_burntoecurechipset` is registered in the command table at file offset
`0xdeddc` with its handler at VA `0x00c5d6fc` (image link base `0x00C00000`, so
VA = file offset + `0xC00000`). Following the veneer table at `0xc5d7c8` reaches
the real implementations:

| Veneer | Target | Function |
|---|---|---|
| `0xc5d7e0` | `0xc5dd8c` | `get_secure_chip_flag(int *out)` |
| `0xc5d7e8` | `0xc5e048` | burn to secure |
| `0xc5d7ec` | `0xc5df6c` | burn to normal |
| `0xc5d8f4` | `0xc5e378` | `otp_read_word(off, u32 *out)` |
| — | `0xc5e8cc` | OTP read primitive, used by both of the above |
| — | `0xc5e910` | OTP write-byte primitive |

`get_secure_chip_flag` is short and unambiguous:

```asm
c5dda4:  add   r1, sp, #12
c5dda8:  mov   r0, #16            ; OTP offset 0x10
c5ddac:  bl    0xc5e8cc           ; otp_read_word(0x10, &val)
c5ddb0:  cmp   r0, #0
c5ddb4:  bne   error
c5ddb8:  ldr   r3, [sp, #12]
c5ddbc:  ands  r3, r3, #0x400     ; bit 10
c5ddc0:  streq r3, [r4]           ; clear -> *out = 0
c5ddc4:  movne r3, #1
c5ddcc:  strne r3, [r4]           ; set   -> *out = 1
```

**The flag is bit 10 (`0x400`) of the OTP word at offset `0x10`.**

Note that `get_secure_chip_flag` and `otpreadall` both go through the same
primitive at `0xc5e8cc`, so a word printed by `otpreadall` can be tested
directly — no endianness correction is needed.

## Checking a board

From the `fastboot#` prompt:

```
otpreadall
```

Read the word at offset `0x10` — it is the first value on the row labelled
`0010` — and mask it with `0x400`.

On the unit examined:

```
0010 80480000 ...
     0x80480000 & 0x400 = 0   ->  not a secure chipset
```

## Independent cross-check

`burn to secure` at `0xc5e048` writes a magic value into OTP starting at offset
`0xa8`:

```asm
c5e070:  mov r0, #0xa8
c5e074:  bl  0xc5e8cc             ; read word at 0xa8
c5e090:  movw r3, #0xe953
c5e094:  movt r3, #0x6edb         ; compare against 0x6edbe953
c5e098:  cmp  r2, r3
c5e09c:  beq  already_done
         ...
c5e0c8:  mov r1, #0x53 ; mov r0, #0xa8 ; bl 0xc5e910
c5e0dc:  mov r1, #0xe9 ; mov r0, #0xa9 ; bl 0xc5e910
c5e0f0:  mov r1, #0xdb ; mov r0, #0xaa ; bl 0xc5e910
```

So a burned part carries `0x6edbe953` at offset `0xa8`. On the unit examined
that word is something else entirely, agreeing with the bit-10 result. Two
independent signals, same conclusion.

## What this does and does not prove

It proves the **vendor bootloader** considers the part non-secure. Inferring
that the **BootROM** therefore does not enforce image signatures is reasonable —
the vendor code exists precisely to control that behaviour — but it is an
inference. The only conclusive test is flashing an unsigned bootloader, which is
also the test that can brick the box.

Before doing that, have a recovery path. The boot log mentions
`enter the gpio press revocery`, so a button-triggered recovery mode exists;
identify it and verify it works first.

## Do not run these

`otp_burntoecurechipset` burns the fuse. It is **one-way**: once set, the SoC
refuses unsigned bootloaders permanently and this entire project becomes
impossible on that unit. `otpwrite` and `otp_setstbprivdata` are likewise
one-way writes.

The full OTP dump is deliberately not committed to this repository — it contains
what appears to be chip-unique key material.
