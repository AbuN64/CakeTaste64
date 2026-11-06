# RageWarsDecomp a67227a0 Makefile:454 and :464, the two keep_dates.py calls (tabs kept) that hosts/n64recomp/PINS RageWars builds with
	$(Q)test -e build/.extract-run-$* || exit 0; python3 -I $(MKTOOLS)/keep_dates.py save $*
	$(Q)test -e build/.extract-run-$* || exit 0; python3 -I $(MKTOOLS)/keep_dates.py restore $*
