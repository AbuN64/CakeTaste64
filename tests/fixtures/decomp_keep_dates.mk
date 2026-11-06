# CakeUnbake64 36ea22de Makefile.in:127 and :137; setup renders these calls for the oracle's generated Makefile.
	$(Q)test -e $(BUILD)/.extract-run-$* || exit 0; python3 -I $(MKTOOLS)/keep_dates.py $(ASM) $(BUILD) save $*
	$(Q)test -e $(BUILD)/.extract-run-$* || exit 0; python3 -I $(MKTOOLS)/keep_dates.py $(ASM) $(BUILD) restore $*
